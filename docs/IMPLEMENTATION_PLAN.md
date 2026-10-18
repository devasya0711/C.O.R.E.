### **Phase 1: Repository Initialization, Docker Infrastructure & CI/CD**

-   **Goal:** Configure Git identity, containerize PostgreSQL, Neo4j
    > (APOC/GDS), Redis, Backend, and Frontend, and create the GitHub
    > Actions CI pipeline.

-   **Tasks:**

    1.  Configure Git author (devasya0711 / devasyakachru.007@gmail.com)
        > and root .gitignore / .env.example.

    2.  Create docker-compose.yml with healthchecks for Postgres, Neo4j,
        > and Redis.

    3.  Scaffold backend/ (FastAPI + pyproject.toml) and frontend/
        > (Next.js 15 + Tailwind + Shadcn UI).

    4.  Create sample_monolith/ (an realistic 6-module Python e-commerce
        > monolith with entangled auth, orders, payments, inventory, and
        > db_models plus a pytest suite).

    5.  Create .github/workflows/ci.yml running Ruff, Pytest, TypeScript
        > check, and Docker builds.

-   **Files Affected:** docker-compose.yml, .github/workflows/ci.yml,
    > sample_monolith/**, backend/app/main.py, frontend/package.json.

-   **Tests Required:** GET /api/v1/health returns 200 OK; pytest runs
    > cleanly on sample_monolith.

-   **Completion Criteria:** All containers boot cleanly via docker
    > compose up -d and initial commit is pushed to GitHub.

### **Phase 2: Database Layer, Clerk Auth & Security Middleware**

-   **Goal:** Implement PostgreSQL models/migrations, Neo4j
    > constraints/vector index initialization, Clerk JWT verification,
    > and Redis rate limiting.

-   **Tasks:**

    1.  Implement SQLAlchemy async models (User, Repository,
        > SeamCluster, MigrationJob) and initial Alembic migration.

    2.  Implement Neo4j async driver wrapper and startup
        > constraint/vector index initializer.

    3.  Implement Clerk JWT verification dependency (get_current_user)
        > and Fernet token encryption utility.

    4.  Implement Redis sliding-window rate limiter middleware.

-   **Files Affected:** backend/app/db/**,
    > backend/app/core/security.py, backend/alembic/**,
    > frontend/src/middleware.ts.

-   **Tests Required:** Auth unit tests (reject missing/invalid JWTs),
    > DB CRUD tests, and Neo4j constraint verification.

-   **Completion Criteria:** Protected API routes enforce Clerk auth and
    > database migrations apply cleanly.

### **Phase 3: Agent 1 — Tree-Sitter AST Parser & Hybrid GraphRAG Ingestion**

-   **Goal:** Parse repositories into Neo4j nodes/edges and attach
    > vector embeddings.

-   **Tasks:**

    1.  Build StaticAnalyzerAgent using tree-sitter-python to extract
        > File, Class, Function, APIEndpoint, and DBTable nodes and
        > CALLS, INHERITS, READS_FROM, WRITES_TO edges.

    2.  Build embedding pipeline for function docstrings/bodies and
        > batch-upsert into Neo4j.

    3.  Expose POST /api/v1/repos, GET /api/v1/repos, and GET
        > /api/v1/repos/{repo_id}/graph.

-   **Files Affected:** backend/app/agents/static_analyzer.py,
    > backend/app/services/neo4j_service.py,
    > backend/app/api/v1/repos.py.

-   **Tests Required:** test_static_analyzer.py verifying exact node and
    > CALLS edge counts on sample_monolith.

-   **Completion Criteria:** Ingesting sample_monolith populates Neo4j
    > and returns a valid React Flow graph JSON payload.

### **Phase 4: Agent 2 & Agent 3 — Boundary Mapper & Blast Radius Predictor**

-   **Goal:** Implement Louvain community seam clustering and 1-to-5 hop
    > Blast Radius analysis.

-   **Tasks:**

    1.  Implement BoundaryMapperAgent (POST
        > /api/v1/repos/{repo_id}/seams/analyze) using Neo4j GDS Louvain
        > (with NetworkX Louvain fallback) + shared DB table conflict
        > detection.

    2.  Implement ImpactPredictorAgent (POST
        > /api/v1/repos/{repo_id}/impact) running variable-length Cypher
        > queries (1..5 hops) and computing the Blast Radius Risk Score
        > (0–100).

-   **Files Affected:** backend/app/agents/boundary_mapper.py,
    > backend/app/agents/impact_predictor.py,
    > backend/app/api/v1/analysis.py.

-   **Tests Required:** test_boundary_and_impact.py verifying multi-hop
    > caller recall on PaymentProcessor.execute.

-   **Completion Criteria:** Both endpoints return deterministic graph
    > clusters and multi-hop blast radius paths.

### **Phase 5: Agent 4, CRAG Sandbox Loop & GitHub PR Automation**

-   **Goal:** Build the LangGraph state machine with CRAG context
    > verification, code generation, pytest sandbox self-correction, and
    > GitHub PR creation.

-   **Tasks:**

    1.  Implement CRAGEvaluatorNode to verify retrieved subgraph
        > completeness and fetch full AST source files on ambiguity.

    2.  Implement RefactorCoderAgent generating extracted FastAPI
        > service files, Dockerfile, NGINX Strangler Fig config, and
        > monolith HTTP adapter.

    3.  Implement SandboxRunnerService running pytest in an isolated
        > temporary copy with up to 3 self-healing retries.

    4.  Implement GitHubService (PyGithub) to create a branch, commit
        > artifacts, and open a PR with a Mermaid diagram.

    5.  Wire everything into backend/app/workflows/migration_graph.py
        > with SSE streaming (GET /migrations/{job_id}/stream).

-   **Files Affected:** backend/app/workflows/migration_graph.py,
    > backend/app/agents/crag_evaluator.py,
    > backend/app/agents/refactor_coder.py,
    > backend/app/services/sandbox_runner.py,
    > backend/app/services/github_service.py.

-   **Tests Required:** End-to-end test extracting the payments seam
    > from sample_monolith and passing the sandbox pytest check.

-   **Completion Criteria:** LangGraph workflow completes extraction,
    > passes sandbox tests, and streams state events.

### **Phase 6: Industrial Dark-Mode Frontend Studio & Final Polish**

-   **Goal:** Build the Next.js 15 UI following DESIGN.md, connect all
    > APIs, add E2E tests, and finalize documentation.

-   **Tasks:**

    1.  Build Dashboard (/dashboard), Command Palette (Cmd+K), and
        > Settings (/settings).

    2.  Build Architecture Graph Studio (/repos/[repoId]/graph) with
        > custom React Flow nodes, Seam Cluster coloring toggle, and
        > animated red Blast Radius path highlighting.

    3.  Build Migration Orchestrator
        > (/repos/[repoId]/migrations/[jobId]) with live SSE
        > stepper, CRAG retry logs, and code diff viewer.

-   **Files Affected:** frontend/src/app/**,
    > frontend/src/components/**.

-   **Tests Required:** Frontend production build (npm run build) and
    > Playwright E2E smoke test.

-   **Completion Criteria:** Full application runs via Docker Compose,
    > passes CI, and is pushed to
    > [https://github.com/devasya0711/C.O.R.E..git](https://github.com/devasya0711/C.O.R.E..git).

**Recommended First Task:** Execute **Prompt 8 (Project Setup Prompt)**
below to initialize the repository, set your Git identity (devasya0711),
build sample_monolith, and bring up the Docker Compose stack.
