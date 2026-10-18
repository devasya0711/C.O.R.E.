# Product Requirements Document (PRD): C.O.R.E.

**Product Name:** C.O.R.E. (Codebase Orchestration & Refactoring
Engine)

**Repository:** `https://github.com/devasya0711/C.O.R.E..git`

**Author:** devasya0711 (`devasyakachru.007@gmail.com`)

**Status:** Approved for MVP Implementation

---

## 1. Problem, Value Proposition & Target Users

### 1.1 Problem Statement

Enterprise engineering teams maintain monolithic codebases where hidden
call dependencies and shared database tables make refactoring hazardous.
Standard LLM coding assistants operate on text chunks and limited
context windows; they lack deterministic structural awareness across
multi-hop function calls and database writes, leading to broken builds
when attempting service extraction.

### 1.2 Value Proposition

C.O.R.E. is a Multi-Agent GraphRAG SaaS platform that parses
repositories via Abstract Syntax Trees (Tree-sitter) into a Neo4j
Knowledge Graph, applies mathematical community detection (Louvain
algorithm) to identify low-coupling microservice seams, predicts
multi-hop "blast radius" impact before code changes, and executes
self-healing refactoring verified inside an isolated test sandbox before
opening a GitHub Pull Request.

### 1.3 Target Users

- **Primary:** Staff/Senior Software Engineers & Platform
Architects migrating legacy Python/Java monoliths into microservices.

- **Secondary:** Engineering Managers evaluating technical debt,
coupling scores, and refactoring risk.

---

## 2. Assumptions & Decisions Requiring Approval

- **[ASSUMPTION-1]:** MVP AST parsing will fully support
**Python** (with extensible hooks for Java/TypeScript) to ensure
deterministic end-to-end execution within MVP scope.

- **[ASSUMPTION-2]:** Repository ingestion in MVP supports public
GitHub URLs, private GitHub repos via fine-grained Personal Access
Tokens (PAT) / GitHub App, or local `.zip` uploads up to 100 MB.

- **[APPROVAL REQUIRED-1]:** LLM Provider Selection — Defaulting
to OpenAI (`gpt-4o` + `text-embedding-3-small`) or Google Gemini
(`gemini-2.5-pro`) via configurable `.env` keys. Confirm preferred
primary provider.

---

## 3. Core User Journeys & User Stories

### Journey 1: Repository Ingestion & Dependency Graph Construction

- **US-1.1:** As an Architect, I want to submit a GitHub repository
URL and branch so that C.O.R.E. parses its AST and builds an interactive
Neo4j Knowledge Graph.

- **US-1.2:** As an Architect, I want to view real-time ingestion
progress (Cloning -> AST Parsing -> Vector Embedding -> Graph
Indexing) so I know when analysis is ready.

### Journey 2: Microservice Seam Discovery (Boundary Mapping)

- **US-2.1:** As an Architect, I want to run Louvain Community
Detection on my code graph so I can visually inspect cohesive clusters
("seams") that are candidates for microservice extraction.

- **US-2.2:** As an Architect, I want C.O.R.E. to highlight shared
database tables (`WRITES_TO` / `READS_FROM` conflicts across
communities) so I can spot data decomposition bottlenecks.

### Journey 3: Multi-Hop Blast Radius Simulation

- **US-3.1:** As a Developer, I want to select any function or
class node (e.g., `PaymentProcessor.execute`) and simulate a signature
change to see all 1-to-5 hop upstream callers and affected API routes
illuminated on the graph.

- **US-3.2:** As a Developer, I want a quantified Blast Radius Risk
Score (0–100) explaining why a change is safe or high-risk.

### Journey 4: Execution-Verified Refactoring & Automated PR

- **US-4.1:** As a Developer, I want to click "Extract
Microservice" on an identified seam so the LangGraph multi-agent system
generates the decoupled FastAPI service, Strangler Fig proxy
configuration, and monolith adapter code.

- **US-4.2:** As a Developer, I want the CRAG Evaluator to test the
generated code inside an ephemeral `pytest` sandbox (up to 3
self-correction loops) before pushing a branch and opening a GitHub Pull
Request.

---

## 4. Features & Functional Requirements

| ID | Feature | Functional Requirement |

|---|---|---|

| **FR-1** | **Agent 1: Static Analyzer** | Parse source
files with `tree-sitter` to extract `File`, `Class`, `Function`,
`API_Endpoint`, and `DB_Table` nodes, plus `CALLS`, `INHERITS`,
`READS_FROM`, and `WRITES_TO` edges. Attach docstring/code vector
embeddings to `Function` nodes. |

| **FR-2** | **Agent 2: Boundary Mapper** | Execute Louvain
community detection via Neo4j GDS (with NetworkX fallback) to group
nodes into microservice candidates, compute cohesion/coupling ratios,
and flag shared DB table conflicts. |

| **FR-3** | **Agent 3: Impact Predictor** | Execute
variable-length Cypher queries (`1..5` hops) from a target node,
returning affected nodes, edges, API endpoints, and a calculated Blast
Radius Risk Score (0–100). |

| **FR-4** | **CRAG Retrieval Evaluator** | Grade retrieved
subgraph context for completeness (checking unresolved imports or
missing type definitions). Trigger fallback full-file AST retrieval if
context is ambiguous. |

| **FR-5** | **Agent 4: Refactor Coder & Sandbox** | Generate
extracted microservice files, Dockerfile, NGINX/Envoy Strangler Fig
config, and adapter code. Run `pytest` in an isolated sandbox; route
`stderr` back to Agent 4 for up to 3 retries on failure. |

| **FR-6** | **GitHub PR Engine** | Create a branch
(`refactor/extract-{seam}`), commit verified files, and open a Pull
Request containing a Mermaid dependency diagram and Blast Radius summary
table. |

---

## 5. Non-Functional Requirements

- **Performance:** Graph queries for Blast Radius (up to 5 hops on
10,000 nodes) must return in under 500ms. Initial repo ingestion (up to
500 files) must complete in under 90 seconds.

- **Security:** Clerk JWT authentication on all API routes; AES-256
encryption at rest for stored GitHub tokens; parameterized Cypher
queries (zero string interpolation); isolated subprocess/container
sandbox with no host network privileges.

- **Reliability:** Idempotent graph ingestion (re-ingesting a
commit cleanly updates existing nodes without duplicates).

---

## 6. Pages & Screens

1. **Landing & Auth (`/`, `/sign-in`, `/sign-up`):**
Clerk-authenticated entry experience with system architecture preview.

2. **Workspace Dashboard (`/dashboard`):** List of ingested
repositories, ingestion status badges, coupling health metrics, and
"Import Repository" modal.

3. **Architecture Graph Studio (`/repos/[repoId]/graph`):**
Full-screen interactive React Flow canvas with Seam Cluster coloring
toggle, Node Inspector drawer, and Blast Radius Simulator panel.

4. **Migration Orchestrator
(`/repos/[repoId]/migrations/[jobId]`):** Real-time Server-Sent
Events (SSE) stepper showing agent execution logs, CRAG sandbox test
iterations, side-by-side code diff viewer, and "Create GitHub PR"
action.

5. **Settings (`/settings`):** GitHub token management and LLM
configuration.

---

## 7. Roles & Permissions

- **Authenticated Workspace Owner (MVP Default):** Full CRUD access
to their own ingested repositories, graph analyses, migration jobs, and
GitHub integrations. Strict tenant isolation enforced via `user_id` on
all PostgreSQL records and `repo_id` namespaces on all Neo4j nodes.

---

## 8. Data Requirements

- **PostgreSQL:** Relational metadata (`users`, `repositories`,
`ingestion_jobs`, `seam_clusters`, `migration_jobs`, encrypted
`github_integrations`).

- **Neo4j:** Code Knowledge Graph (`CodeNode` labels: `File`,
`Class`, `Function`, `APIEndpoint`, `DBTable`; edges: `CALLS`,
`INHERITS`, `READS_FROM`, `WRITES_TO`, `CONTAINS`), partitioned
by `repo_id`.

- **Redis:** API rate limiting counters, ingestion job progress
pub/sub, and cached Blast Radius responses.

---

## 9. Integrations

- **Clerk:** Authentication and session management.

- **GitHub REST API (`PyGithub`):** Repository cloning, branch
creation, file commits, and Pull Request creation.

- **LLM API (OpenAI / Gemini):** Semantic embeddings, CRAG grading,
and Refactor Coder synthesis.

---

## 10. Loading, Empty & Error States

- **Loading:** Skeleton loaders on repository cards; live
multi-stage progress bar with SSE terminal stream during AST ingestion
and CRAG sandbox testing.

- **Empty:** Interactive "Load Demo Monolith (`sample_monolith`)"
button when a new user has zero repositories ingested.

- **Error:** Structured error banners displaying exact failure
stage (e.g., `AST_PARSE_ERROR`, `SANDBOX_TIMEOUT`,
`GITHUB_AUTH_FAILED`) with a one-click retry action.

---

## 11. MVP Scope, Out-of-Scope & Success Criteria

- **In Scope (MVP):** Python AST ingestion, Neo4j Graph + Vector
hybrid store, Louvain seam detection, 1–5 hop Blast Radius simulator,
4-Agent LangGraph workflow with `pytest` CRAG loop, GitHub PR
creation, Next.js React Flow studio, Docker Compose setup, and GitHub
Actions CI.

- **Out of Scope:** Live production Kubernetes deployment of
extracted services, automated database data migration scripts (only
schema coupling warnings are generated), billing/Stripe integration.

- **Success Criteria:**

1. Successfully ingest `sample_monolith` and render its complete
dependency graph in under 30 seconds.

2. Accurately trace 100% of 3-hop callers in the Blast Radius
simulator.

3. Complete an end-to-end microservice extraction where the CRAG loop
passes `pytest` and opens a formatted PR on GitHub.
