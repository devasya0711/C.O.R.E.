# System Architecture Specification: C.O.R.E.

## 1. Recommended Tech Stack & Reasoning

- **Frontend:** Next.js 15 (App Router, TypeScript), Tailwind CSS,
Shadcn UI, `@xyflow/react` (React Flow) for high-performance node-link
rendering.

- **Backend API & Orchestration:** Python 3.12, FastAPI (async
REST + Server-Sent Events), LangGraph (stateful cyclical agent
orchestration required for the CRAG retry loop).

- **AST & Static Analysis:** `tree-sitter` +
`tree-sitter-python` for deterministic, fast syntax tree extraction
without executing untrusted code during ingestion.

- **Graph + Vector Store:** Neo4j 5 Community/Enterprise with APOC
& Graph Data Science (GDS) plugins (stores both deterministic AST
topology and vector embeddings on nodes).

- **Relational Store:** PostgreSQL 16 (via SQLAlchemy 2.0 Async +
Alembic) for user workspaces, job state, and encrypted credentials.

- **Cache & Pub/Sub:** Redis 7 for API rate limiting, Blast Radius
query caching, and streaming LangGraph SSE events.

- **Authentication:** Clerk (Next.js middleware + FastAPI JWKS JWT
verification).

---

## 2. High-Level Data Flow

1. **Ingestion Flow:** Client submits Repo URL -> FastAPI creates
`IngestionJob` in Postgres -> Worker clones repo to ephemeral volume
-> **Agent 1 (Static Analyzer)** parses AST via Tree-sitter,
generates embeddings, and batch-upserts nodes/edges into Neo4j ->
Updates job status via Redis SSE.

2. **Analysis Flow (Seams & Blast Radius):** Client requests seams
or impact -> **Agent 2 (Boundary Mapper)** runs Louvain clustering
on Neo4j subgraph; **Agent 3 (Impact Predictor)** runs
variable-length Cypher traversal -> Results cached in Redis and
returned to React Flow canvas.

3. **Migration & CRAG Flow:** User triggers extraction on a Seam
-> **LangGraph Orchestrator** invokes **CRAG Retrieval
Evaluator** (fetches missing AST source files if graph context is
incomplete) -> **Agent 4 (Refactor Coder)** generates
microservice + Strangler Fig adapter -> **Execution Sandbox** runs
`pytest` in isolated subprocess/container -> If failed (`retry <
3`), routes `stderr` back to Agent 4 -> On pass, **GitHub
Service** pushes branch and opens PR.

---

## 3. Monorepo Folder Structure

```text

C.O.R.E./

├── .github/workflows/

│ └── ci.yml # Lint, Pytest, Typecheck, Docker build

├── backend/

│ ├── alembic/ # PostgreSQL migrations

│ ├── app/

│ │ ├── api/v1/ # FastAPI routers (repos, graph, migrations, webhooks)

│ │ ├── agents/ # LangGraph agents

│ │ │ ├── static_analyzer.py

│ │ │ ├── boundary_mapper.py

│ │ │ ├── impact_predictor.py

│ │ │ ├── crag_evaluator.py

│ │ │ └── refactor_coder.py

│ │ ├── core/ # Config, Clerk JWT auth, security, logging, rate-limit

│ │ ├── db/ # Postgres session, SQLAlchemy models, Neo4j & Redis
clients

│ │ ├── schemas/ # Pydantic v2 request/response schemas

│ │ ├── services/ # GitHub service, Sandbox runner, Embedding service

│ │ ├── workflows/ # LangGraph state machine (migration_graph.py)

│ │ └── main.py # FastAPI app entrypoint

│ ├── tests/ # Pytest unit & integration suites

│ ├── Dockerfile

│ └── pyproject.toml

├── frontend/

│ ├── src/

│ │ ├── app/ # Next.js App Router pages

│ │ ├── components/ # Graph canvas, custom nodes, stepper, Shadcn
primitives

│ │ ├── lib/ # API client, SSE hooks, utils

│ │ └── types/ # Shared TypeScript interfaces

│ ├── Dockerfile

│ └── package.json

├── sample_monolith/ # Built-in target Python monolith for instant
testing & demos

├── docker-compose.yml

└── .env.example

## **4. AI & LangGraph Architecture**

migration_graph.py defines a StateGraph(MigrationState) with
deterministic conditional edges:

-   retrieve_seam_context -> crag_evaluate_context

-   Conditional Edge from crag_evaluate_context:

    -   "context_sufficient" -> generate_refactor (Agent 4)

    -   "context_incomplete" -> augment_ast_source ->
        > generate_refactor

-   generate_refactor -> execute_sandbox_tests

-   Conditional Edge from execute_sandbox_tests:

    -   "tests_passed" -> create_github_pr -> END

    -   "tests_failed_retry" (retry_count < 3) -> generate_refactor
        > (with stderr trace)

    -   "max_retries_exceeded" -> mark_for_human_review -> END

## **5. Security Boundaries & Error Handling**

-   **Tenant Isolation:** Every PostgreSQL query filters by
    > authenticated user_id. Every Neo4j Cypher query filters by
    > {repo_id: $repo_id} after verifying the user owns repo_id.

-   **Cypher Injection Prevention:** All Neo4j queries use strict
    > parameterized bindings ($repo_id, $node_id). Dynamic hop depths
    > are validated as bounded integers (1 <= hops <= 5) in Pydantic
    > before query construction.

-   **Sandbox Containment:** The pytest verification runner executes
    > inside a temporary directory with a strict 30-second timeout and
    > stripped environment variables so user repositories cannot read
    > C.O.R.E. application secrets.

---
