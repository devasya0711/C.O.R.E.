## **1. PRD.md**

Markdown

\# Product Requirements Document (PRD): C.O.R.E.

\*\*Product Name:\*\* C.O.R.E. (Codebase Orchestration & Refactoring
Engine)

\*\*Repository:\*\* \`https://github.com/devasya0711/C.O.R.E..git\`

\*\*Author:\*\* devasya0711 (\`devasyakachru.007@gmail.com\`)

\*\*Status:\*\* Approved for MVP Implementation

---

\## 1. Problem, Value Proposition & Target Users

\### 1.1 Problem Statement

Enterprise engineering teams maintain monolithic codebases where hidden
call dependencies and shared database tables make refactoring hazardous.
Standard LLM coding assistants operate on text chunks and limited
context windows; they lack deterministic structural awareness across
multi-hop function calls and database writes, leading to broken builds
when attempting service extraction.

\### 1.2 Value Proposition

C.O.R.E. is a Multi-Agent GraphRAG SaaS platform that parses
repositories via Abstract Syntax Trees (Tree-sitter) into a Neo4j
Knowledge Graph, applies mathematical community detection (Louvain
algorithm) to identify low-coupling microservice seams, predicts
multi-hop "blast radius" impact before code changes, and executes
self-healing refactoring verified inside an isolated test sandbox before
opening a GitHub Pull Request.

\### 1.3 Target Users

\- \*\*Primary:\*\* Staff/Senior Software Engineers & Platform
Architects migrating legacy Python/Java monoliths into microservices.

\- \*\*Secondary:\*\* Engineering Managers evaluating technical debt,
coupling scores, and refactoring risk.

---

\## 2. Assumptions & Decisions Requiring Approval

\- \*\*\[ASSUMPTION-1\]:\*\* MVP AST parsing will fully support
\*\*Python\*\* (with extensible hooks for Java/TypeScript) to ensure
deterministic end-to-end execution within MVP scope.

\- \*\*\[ASSUMPTION-2\]:\*\* Repository ingestion in MVP supports public
GitHub URLs, private GitHub repos via fine-grained Personal Access
Tokens (PAT) / GitHub App, or local \`.zip\` uploads up to 100 MB.

\- \*\*\[APPROVAL REQUIRED-1\]:\*\* LLM Provider Selection — Defaulting
to OpenAI (\`gpt-4o\` + \`text-embedding-3-small\`) or Google Gemini
(\`gemini-2.5-pro\`) via configurable \`.env\` keys. Confirm preferred
primary provider.

---

\## 3. Core User Journeys & User Stories

\### Journey 1: Repository Ingestion & Dependency Graph Construction

\- \*\*US-1.1:\*\* As an Architect, I want to submit a GitHub repository
URL and branch so that C.O.R.E. parses its AST and builds an interactive
Neo4j Knowledge Graph.

\- \*\*US-1.2:\*\* As an Architect, I want to view real-time ingestion
progress (Cloning -\> AST Parsing -\> Vector Embedding -\> Graph
Indexing) so I know when analysis is ready.

\### Journey 2: Microservice Seam Discovery (Boundary Mapping)

\- \*\*US-2.1:\*\* As an Architect, I want to run Louvain Community
Detection on my code graph so I can visually inspect cohesive clusters
("seams") that are candidates for microservice extraction.

\- \*\*US-2.2:\*\* As an Architect, I want C.O.R.E. to highlight shared
database tables (\`WRITES_TO\` / \`READS_FROM\` conflicts across
communities) so I can spot data decomposition bottlenecks.

\### Journey 3: Multi-Hop Blast Radius Simulation

\- \*\*US-3.1:\*\* As a Developer, I want to select any function or
class node (e.g., \`PaymentProcessor.execute\`) and simulate a signature
change to see all 1-to-5 hop upstream callers and affected API routes
illuminated on the graph.

\- \*\*US-3.2:\*\* As a Developer, I want a quantified Blast Radius Risk
Score (0–100) explaining why a change is safe or high-risk.

\### Journey 4: Execution-Verified Refactoring & Automated PR

\- \*\*US-4.1:\*\* As a Developer, I want to click "Extract
Microservice" on an identified seam so the LangGraph multi-agent system
generates the decoupled FastAPI service, Strangler Fig proxy
configuration, and monolith adapter code.

\- \*\*US-4.2:\*\* As a Developer, I want the CRAG Evaluator to test the
generated code inside an ephemeral \`pytest\` sandbox (up to 3
self-correction loops) before pushing a branch and opening a GitHub Pull
Request.

---

\## 4. Features & Functional Requirements

\| ID \| Feature \| Functional Requirement \|

\|---\|---\|---\|

\| \*\*FR-1\*\* \| \*\*Agent 1: Static Analyzer\*\* \| Parse source
files with \`tree-sitter\` to extract \`File\`, \`Class\`, \`Function\`,
\`API_Endpoint\`, and \`DB_Table\` nodes, plus \`CALLS\`, \`INHERITS\`,
\`READS_FROM\`, and \`WRITES_TO\` edges. Attach docstring/code vector
embeddings to \`Function\` nodes. \|

\| \*\*FR-2\*\* \| \*\*Agent 2: Boundary Mapper\*\* \| Execute Louvain
community detection via Neo4j GDS (with NetworkX fallback) to group
nodes into microservice candidates, compute cohesion/coupling ratios,
and flag shared DB table conflicts. \|

\| \*\*FR-3\*\* \| \*\*Agent 3: Impact Predictor\*\* \| Execute
variable-length Cypher queries (\`1..5\` hops) from a target node,
returning affected nodes, edges, API endpoints, and a calculated Blast
Radius Risk Score (0–100). \|

\| \*\*FR-4\*\* \| \*\*CRAG Retrieval Evaluator\*\* \| Grade retrieved
subgraph context for completeness (checking unresolved imports or
missing type definitions). Trigger fallback full-file AST retrieval if
context is ambiguous. \|

\| \*\*FR-5\*\* \| \*\*Agent 4: Refactor Coder & Sandbox\*\* \| Generate
extracted microservice files, Dockerfile, NGINX/Envoy Strangler Fig
config, and adapter code. Run \`pytest\` in an isolated sandbox; route
\`stderr\` back to Agent 4 for up to 3 retries on failure. \|

\| \*\*FR-6\*\* \| \*\*GitHub PR Engine\*\* \| Create a branch
(\`refactor/extract-{seam}\`), commit verified files, and open a Pull
Request containing a Mermaid dependency diagram and Blast Radius summary
table. \|

---

\## 5. Non-Functional Requirements

\- \*\*Performance:\*\* Graph queries for Blast Radius (up to 5 hops on
10,000 nodes) must return in under 500ms. Initial repo ingestion (up to
500 files) must complete in under 90 seconds.

\- \*\*Security:\*\* Clerk JWT authentication on all API routes; AES-256
encryption at rest for stored GitHub tokens; parameterized Cypher
queries (zero string interpolation); isolated subprocess/container
sandbox with no host network privileges.

\- \*\*Reliability:\*\* Idempotent graph ingestion (re-ingesting a
commit cleanly updates existing nodes without duplicates).

---

\## 6. Pages & Screens

1\. \*\*Landing & Auth (\`/\`, \`/sign-in\`, \`/sign-up\`):\*\*
Clerk-authenticated entry experience with system architecture preview.

2\. \*\*Workspace Dashboard (\`/dashboard\`):\*\* List of ingested
repositories, ingestion status badges, coupling health metrics, and
"Import Repository" modal.

3\. \*\*Architecture Graph Studio (\`/repos/\[repoId\]/graph\`):\*\*
Full-screen interactive React Flow canvas with Seam Cluster coloring
toggle, Node Inspector drawer, and Blast Radius Simulator panel.

4\. \*\*Migration Orchestrator
(\`/repos/\[repoId\]/migrations/\[jobId\]\`):\*\* Real-time Server-Sent
Events (SSE) stepper showing agent execution logs, CRAG sandbox test
iterations, side-by-side code diff viewer, and "Create GitHub PR"
action.

5\. \*\*Settings (\`/settings\`):\*\* GitHub token management and LLM
configuration.

---

\## 7. Roles & Permissions

\- \*\*Authenticated Workspace Owner (MVP Default):\*\* Full CRUD access
to their own ingested repositories, graph analyses, migration jobs, and
GitHub integrations. Strict tenant isolation enforced via \`user_id\` on
all PostgreSQL records and \`repo_id\` namespaces on all Neo4j nodes.

---

\## 8. Data Requirements

\- \*\*PostgreSQL:\*\* Relational metadata (\`users\`, \`repositories\`,
\`ingestion_jobs\`, \`seam_clusters\`, \`migration_jobs\`, encrypted
\`github_integrations\`).

\- \*\*Neo4j:\*\* Code Knowledge Graph (\`CodeNode\` labels: \`File\`,
\`Class\`, \`Function\`, \`APIEndpoint\`, \`DBTable\`; edges: \`CALLS\`,
\`INHERITS\`, \`READS_FROM\`, \`WRITES_TO\`, \`CONTAINS\`), partitioned
by \`repo_id\`.

\- \*\*Redis:\*\* API rate limiting counters, ingestion job progress
pub/sub, and cached Blast Radius responses.

---

\## 9. Integrations

\- \*\*Clerk:\*\* Authentication and session management.

\- \*\*GitHub REST API (\`PyGithub\`):\*\* Repository cloning, branch
creation, file commits, and Pull Request creation.

\- \*\*LLM API (OpenAI / Gemini):\*\* Semantic embeddings, CRAG grading,
and Refactor Coder synthesis.

---

\## 10. Loading, Empty & Error States

\- \*\*Loading:\*\* Skeleton loaders on repository cards; live
multi-stage progress bar with SSE terminal stream during AST ingestion
and CRAG sandbox testing.

\- \*\*Empty:\*\* Interactive "Load Demo Monolith (\`sample_monolith\`)"
button when a new user has zero repositories ingested.

\- \*\*Error:\*\* Structured error banners displaying exact failure
stage (e.g., \`AST_PARSE_ERROR\`, \`SANDBOX_TIMEOUT\`,
\`GITHUB_AUTH_FAILED\`) with a one-click retry action.

---

\## 11. MVP Scope, Out-of-Scope & Success Criteria

\- \*\*In Scope (MVP):\*\* Python AST ingestion, Neo4j Graph + Vector
hybrid store, Louvain seam detection, 1–5 hop Blast Radius simulator,
4-Agent LangGraph workflow with \`pytest\` CRAG loop, GitHub PR
creation, Next.js React Flow studio, Docker Compose setup, and GitHub
Actions CI.

\- \*\*Out of Scope:\*\* Live production Kubernetes deployment of
extracted services, automated database data migration scripts (only
schema coupling warnings are generated), billing/Stripe integration.

\- \*\*Success Criteria:\*\*

1\. Successfully ingest \`sample_monolith\` and render its complete
dependency graph in under 30 seconds.

2\. Accurately trace 100% of 3-hop callers in the Blast Radius
simulator.

3\. Complete an end-to-end microservice extraction where the CRAG loop
passes \`pytest\` and opens a formatted PR on GitHub.

## **2. DESIGN.md**

Markdown

\# Design System & UX Specification: C.O.R.E.

\## 1. Visual Direction & Design Principles

C.O.R.E. embodies an \*\*Industrial Precision Dark-Mode First\*\*
aesthetic inspired by Linear, Raycast, and Vercel. It avoids decorative
gradients, marketing fluff, or generic AI sparkles in favor of
high-density technical clarity.

1\. \*\*Topology First:\*\* The interactive code graph is the primary
workspace—chrome and toolbars float unobtrusively with translucent glass
borders.

2\. \*\*Semantic Color Only:\*\* Bright colors are reserved strictly for
graph semantics (Seam clusters, Blast Radius severity, CRAG pass/fail
states).

3\. \*\*Zero-Latency Feedback:\*\* Every graph interaction, node
selection, and streaming agent transition responds in under 100ms.

---

\## 2. Colors & Typography

\### 2.1 Color Palette (Dark Mode Default)

\- \*\*Background Primary (Canvas):\*\* \`#090A0F\` (Obsidian)

\- \*\*Background Elevated (Panels/Drawers):\*\* \`#11131C\` (Carbon
Slate)

\- \*\*Surface Interactive (Cards/Inputs):\*\* \`#181B26\`

\- \*\*Border Hairline:\*\* \`#262938\` (\`1px solid\`)

\- \*\*Border Active/Focus:\*\* \`#4F46E5\` (Indigo 600)

\- \*\*Text Primary:\*\* \`#F3F4F6\` (Gray 100)

\- \*\*Text Secondary:\*\* \`#9CA3AF\` (Gray 400)

\- \*\*Text Muted:\*\* \`#6B7280\` (Gray 500)

\### 2.2 Semantic & Graph Node Tokens

\- \*\*Node - Function:\*\* \`#3B82F6\` (Blue 500)

\- \*\*Node - Class:\*\* \`#8B5CF6\` (Violet 500)

\- \*\*Node - API Endpoint:\*\* \`#10B981\` (Emerald 500)

\- \*\*Node - DB Table:\*\* \`#F59E0B\` (Amber 500)

\- \*\*Blast Radius Path / Critical Risk:\*\* \`#EF4444\` (Red 500 —
animated dashed stroke)

\- \*\*CRAG Verified / Test Pass:\*\* \`#22C55E\` (Green 500)

\### 2.3 Typography

\- \*\*UI Sans Font:\*\* \`Inter\` or \`Geist Sans\` (\`-0.015em\`
letter-spacing on headings).

\- \*\*Code & Graph Node Font:\*\* \`JetBrains Mono\` or \`Geist Mono\`
(used for all function signatures, Cypher badges, risk scores, and
terminal logs).

\- \*\*Type Scale:\*\*

\- \`H1 (Page Title)\`: \`24px / 32px\`, \`font-semibold\`

\- \`H2 (Section Header)\`: \`18px / 26px\`, \`font-semibold\`

\- \`Body Default\`: \`14px / 20px\`, \`font-normal\`

\- \`Mono Technical / Node Label\`: \`12px / 16px\`, \`font-medium\`

---

\## 3. Spacing & Layout System

\- \*\*Base Unit:\*\* \`4px\` grid (\`p-1\` = 4px, \`p-2\` = 8px,
\`p-4\` = 16px, \`p-6\` = 24px).

\- \*\*App Shell:\*\* Fixed left collapsible sidebar (\`240px\`
expanded, \`64px\` collapsed) + Top Command Header (\`56px\` height) +
Main Workspace (\`calc(100vh - 56px)\`).

\- \*\*Border Radius:\*\* Crisp technical radius — \`6px\`
(\`rounded-md\`) for buttons and inputs, \`8px\` (\`rounded-lg\`) for
cards and modals.

---

\## 4. Reusable Components

1\. \*\*Custom React Flow CodeNode:\*\* Displays node type badge
(\`FN\`, \`CLS\`, \`API\`, \`DB\`), monospace symbol name, community
seam color ring, and incoming/outgoing edge count pills.

2\. \*\*Blast Radius Risk Gauge:\*\* Circular or segmented horizontal
meter displaying score \`0-100\`, hop depth selector (\`1\` to \`5\`
hops), and affected endpoint list.

3\. \*\*Agent Execution Stepper:\*\* Vertical timeline showing \`Static
Analyzer\` -\> \`Boundary Mapper\` -\> \`Impact Predictor\` -\> \`CRAG
Evaluator\` -\> \`Refactor Coder\` -\> \`Pytest Sandbox\`, with
expandable monospace log drawers and retry badges.

4\. \*\*Diff & Artifact Viewer:\*\* Tabbed Monaco/Shiki read-only code
viewer displaying generated microservice files, Dockerfile, and
Strangler Fig proxy config.

---

\## 5. Navigation & Forms

\- \*\*Global Command Palette (\`Cmd+K\` / \`Ctrl+K\`):\*\* Quick-jump
to any ingested repository, search for a function node by name across
the active graph, or trigger "Extract Seam".

\- \*\*Forms:\*\* Inline validation with crisp \`1px\` borders; error
messages appear directly below inputs in \`#EF4444\` at \`12px\`.

---

\## 6. Loading, Empty & Error States

\- \*\*Graph Canvas Loading:\*\* Subtle radial dot-grid pulse with a
floating status pill (\`"Computing Louvain community layout..."\`).

\- \*\*Empty State:\*\* Centered card on dark dot-grid canvas offering
two clear actions: \`"Import GitHub Repo"\` or \`"Load Sample E-Commerce
Monolith"\`.

\- \*\*Error State:\*\* Non-destructive inline diagnostic card with
expandable stack trace / stderr drawer and \`"Retry Step"\` button.

---

\## 7. Interactions & Animations

\- \*\*Blast Radius Propagation:\*\* Selecting a node dims unrelated
nodes to \`25%\` opacity (\`transition: opacity 150ms ease\`) and
animates affected \`CALLS\` edges using a crimson marching-ants SVG
stroke (\`stroke-dasharray: 6 4\`).

\- \*\*Seam Cluster Hull:\*\* Toggling "Show Microservice Seams" renders
subtle translucent background groupings around nodes sharing the same
\`community_id\`.

---

\## 8. Responsive Behavior & Accessibility

\- \*\*Desktop-First Studio:\*\* Optimized for \`1280px+\` viewports.
Below \`1024px\`, the Graph Inspector drawer collapses into a bottom
sheet.

\- \*\*Accessibility:\*\* WCAG AA contrast (\`4.5:1\` minimum for text);
full keyboard navigation for the Command Palette (\`Cmd+K\`) and node
list fallback table; \`prefers-reduced-motion\` disables SVG edge
animations.

---

\## 9. Page-Level Layout Guidance

\- \*\*\`/dashboard\`:\*\* 3-column responsive grid of Repository Cards
showing total nodes, identified microservice seams, shared DB warnings,
and last ingestion timestamp.

\- \*\*\`/repos/\[repoId\]/graph\`:\*\* 100% viewport React Flow canvas
(\`bg-\[#090A0F\]\` with \`#1F2430\` dot pattern). Floating top-left
toolbar for filter controls; floating right inspector panel (\`380px\`
wide) for Blast Radius simulation and Seam Extraction trigger.

## **3. ARCHITECTURE.md**

Markdown

\# System Architecture Specification: C.O.R.E.

\## 1. Recommended Tech Stack & Reasoning

\- \*\*Frontend:\*\* Next.js 15 (App Router, TypeScript), Tailwind CSS,
Shadcn UI, \`@xyflow/react\` (React Flow) for high-performance node-link
rendering.

\- \*\*Backend API & Orchestration:\*\* Python 3.12, FastAPI (async
REST + Server-Sent Events), LangGraph (stateful cyclical agent
orchestration required for the CRAG retry loop).

\- \*\*AST & Static Analysis:\*\* \`tree-sitter\` +
\`tree-sitter-python\` for deterministic, fast syntax tree extraction
without executing untrusted code during ingestion.

\- \*\*Graph + Vector Store:\*\* Neo4j 5 Community/Enterprise with APOC
& Graph Data Science (GDS) plugins (stores both deterministic AST
topology and vector embeddings on nodes).

\- \*\*Relational Store:\*\* PostgreSQL 16 (via SQLAlchemy 2.0 Async +
Alembic) for user workspaces, job state, and encrypted credentials.

\- \*\*Cache & Pub/Sub:\*\* Redis 7 for API rate limiting, Blast Radius
query caching, and streaming LangGraph SSE events.

\- \*\*Authentication:\*\* Clerk (Next.js middleware + FastAPI JWKS JWT
verification).

---

\## 2. High-Level Data Flow

1\. \*\*Ingestion Flow:\*\* Client submits Repo URL -\> FastAPI creates
\`IngestionJob\` in Postgres -\> Worker clones repo to ephemeral volume
-\> \*\*Agent 1 (Static Analyzer)\*\* parses AST via Tree-sitter,
generates embeddings, and batch-upserts nodes/edges into Neo4j -\>
Updates job status via Redis SSE.

2\. \*\*Analysis Flow (Seams & Blast Radius):\*\* Client requests seams
or impact -\> \*\*Agent 2 (Boundary Mapper)\*\* runs Louvain clustering
on Neo4j subgraph; \*\*Agent 3 (Impact Predictor)\*\* runs
variable-length Cypher traversal -\> Results cached in Redis and
returned to React Flow canvas.

3\. \*\*Migration & CRAG Flow:\*\* User triggers extraction on a Seam
-\> \*\*LangGraph Orchestrator\*\* invokes \*\*CRAG Retrieval
Evaluator\*\* (fetches missing AST source files if graph context is
incomplete) -\> \*\*Agent 4 (Refactor Coder)\*\* generates
microservice + Strangler Fig adapter -\> \*\*Execution Sandbox\*\* runs
\`pytest\` in isolated subprocess/container -\> If failed (\`retry \<
3\`), routes \`stderr\` back to Agent 4 -\> On pass, \*\*GitHub
Service\*\* pushes branch and opens PR.

---

\## 3. Monorepo Folder Structure

\`\`\`text

C.O.R.E./

├── .github/workflows/

│ └── ci.yml \# Lint, Pytest, Typecheck, Docker build

├── backend/

│ ├── alembic/ \# PostgreSQL migrations

│ ├── app/

│ │ ├── api/v1/ \# FastAPI routers (repos, graph, migrations, webhooks)

│ │ ├── agents/ \# LangGraph agents

│ │ │ ├── static_analyzer.py

│ │ │ ├── boundary_mapper.py

│ │ │ ├── impact_predictor.py

│ │ │ ├── crag_evaluator.py

│ │ │ └── refactor_coder.py

│ │ ├── core/ \# Config, Clerk JWT auth, security, logging, rate-limit

│ │ ├── db/ \# Postgres session, SQLAlchemy models, Neo4j & Redis
clients

│ │ ├── schemas/ \# Pydantic v2 request/response schemas

│ │ ├── services/ \# GitHub service, Sandbox runner, Embedding service

│ │ ├── workflows/ \# LangGraph state machine (migration_graph.py)

│ │ └── main.py \# FastAPI app entrypoint

│ ├── tests/ \# Pytest unit & integration suites

│ ├── Dockerfile

│ └── pyproject.toml

├── frontend/

│ ├── src/

│ │ ├── app/ \# Next.js App Router pages

│ │ ├── components/ \# Graph canvas, custom nodes, stepper, Shadcn
primitives

│ │ ├── lib/ \# API client, SSE hooks, utils

│ │ └── types/ \# Shared TypeScript interfaces

│ ├── Dockerfile

│ └── package.json

├── sample_monolith/ \# Built-in target Python monolith for instant
testing & demos

├── docker-compose.yml

└── .env.example

## **4. AI & LangGraph Architecture**

migration_graph.py defines a StateGraph(MigrationState) with
deterministic conditional edges:

-   retrieve_seam_context -\> crag_evaluate_context

-   Conditional Edge from crag_evaluate_context:

    -   "context_sufficient" -\> generate_refactor (Agent 4)

    -   "context_incomplete" -\> augment_ast_source -\>
        > generate_refactor

-   generate_refactor -\> execute_sandbox_tests

-   Conditional Edge from execute_sandbox_tests:

    -   "tests_passed" -\> create_github_pr -\> END

    -   "tests_failed_retry" (retry_count \< 3) -\> generate_refactor
        > (with stderr trace)

    -   "max_retries_exceeded" -\> mark_for_human_review -\> END

## **5. Security Boundaries & Error Handling**

-   **Tenant Isolation:** Every PostgreSQL query filters by
    > authenticated user_id. Every Neo4j Cypher query filters by
    > {repo_id: $repo_id} after verifying the user owns repo_id.

-   **Cypher Injection Prevention:** All Neo4j queries use strict
    > parameterized bindings ($repo_id, $node_id). Dynamic hop depths
    > are validated as bounded integers (1 \<= hops \<= 5) in Pydantic
    > before query construction.

-   **Sandbox Containment:** The pytest verification runner executes
    > inside a temporary directory with a strict 30-second timeout and
    > stripped environment variables so user repositories cannot read
    > C.O.R.E. application secrets.

---

\## 4. \`DATABASE.md\`

\`\`\`markdown

\# Database Specification: C.O.R.E.

\## 1. Database Technologies

1\. \*\*PostgreSQL 16:\*\* System of record for users, repositories,
ingestion jobs, discovered seam clusters, migration jobs, and encrypted
GitHub tokens.

2\. \*\*Neo4j 5 (with APOC & GDS):\*\* System of record for AST nodes,
call/data relationships, community IDs, and 1536-dimensional code
embeddings.

3\. \*\*Redis 7:\*\* Ephemeral cache for Blast Radius queries (\`TTL:
300s\`), rate-limiting sliding windows, and SSE job event streams.

---

\## 2. PostgreSQL Schema

\### 2.1 \`users\`

\| Column \| Type \| Constraints \| Description \|

\|---\|---\|---\|---\|

\| \`id\` \| \`UUID\` \| \`PK, DEFAULT gen_random_uuid()\` \| Internal
user ID \|

\| \`clerk_id\` \| \`VARCHAR(128)\` \| \`UNIQUE, NOT NULL, INDEX\` \|
Clerk external subject ID \|

\| \`email\` \| \`VARCHAR(255)\` \| \`UNIQUE, NOT NULL\` \| User email
\|

\| \`encrypted_github_pat\` \| \`TEXT\` \| \`NULLABLE\` \| Fernet
AES-encrypted GitHub token \|

\| \`created_at\` \| \`TIMESTAMPTZ\` \| \`NOT NULL, DEFAULT now()\` \|
Creation timestamp \|

\### 2.2 \`repositories\`

\| Column \| Type \| Constraints \| Description \|

\|---\|---\|---\|---\|

\| \`id\` \| \`UUID\` \| \`PK, DEFAULT gen_random_uuid()\` \| Repository
ID (used as \`repo_id\` in Neo4j) \|

\| \`user_id\` \| \`UUID\` \| \`FK -\> users.id ON DELETE CASCADE,
INDEX\` \| Owner user ID \|

\| \`name\` \| \`VARCHAR(255)\` \| \`NOT NULL\` \| Repo display name
(e.g., \`org/monolith\`) \|

\| \`repo_url\` \| \`VARCHAR(512)\` \| \`NOT NULL\` \| GitHub URL or
\`local://sample_monolith\` \|

\| \`default_branch\` \| \`VARCHAR(128)\` \| \`NOT NULL, DEFAULT
'main'\` \| Target branch \|

\| \`last_commit_sha\` \| \`VARCHAR(64)\` \| \`NULLABLE\` \| Last
ingested commit hash \|

\| \`status\` \| \`VARCHAR(32)\` \| \`NOT NULL, DEFAULT 'PENDING'\` \|
\`PENDING\`, \`INGESTING\`, \`READY\`, \`FAILED\` \|

\| \`node_count\` \| \`INTEGER\` \| \`NOT NULL, DEFAULT 0\` \| Total AST
nodes in Neo4j \|

\| \`edge_count\` \| \`INTEGER\` \| \`NOT NULL, DEFAULT 0\` \| Total
relationships in Neo4j \|

\| \`created_at\` \| \`TIMESTAMPTZ\` \| \`NOT NULL, DEFAULT now()\` \|
Creation timestamp \|

\### 2.3 \`seam_clusters\`

\| Column \| Type \| Constraints \| Description \|

\|---\|---\|---\|---\|

\| \`id\` \| \`UUID\` \| \`PK, DEFAULT gen_random_uuid()\` \| Seam
cluster ID \|

\| \`repo_id\` \| \`UUID\` \| \`FK -\> repositories.id ON DELETE
CASCADE, INDEX\` \| Parent repository \|

\| \`community_id\` \| \`INTEGER\` \| \`NOT NULL\` \| Louvain cluster
integer ID \|

\| \`suggested_name\` \| \`VARCHAR(128)\` \| \`NOT NULL\` \|
LLM-synthesized service name (e.g., \`payment-service\`) \|

\| \`cohesion_score\` \| \`FLOAT\` \| \`NOT NULL\` \| Internal vs.
external edge ratio (\`0.0 - 1.0\`) \|

\| \`shared_db_tables\` \| \`JSONB\` \| \`NOT NULL, DEFAULT '\[\]'\` \|
List of DB tables shared with other clusters \|

\| \`node_ids\` \| \`JSONB\` \| \`NOT NULL, DEFAULT '\[\]'\` \| Array of
Neo4j \`qualified_name\` identifiers \|

\### 2.4 \`migration_jobs\`

\| Column \| Type \| Constraints \| Description \|

\|---\|---\|---\|---\|

\| \`id\` \| \`UUID\` \| \`PK, DEFAULT gen_random_uuid()\` \| Migration
job ID \|

\| \`repo_id\` \| \`UUID\` \| \`FK -\> repositories.id ON DELETE
CASCADE, INDEX\` \| Target repository \|

\| \`seam_id\` \| \`UUID\` \| \`FK -\> seam_clusters.id ON DELETE
CASCADE\` \| Target seam cluster \|

\| \`status\` \| \`VARCHAR(32)\` \| \`NOT NULL, DEFAULT 'QUEUED'\` \|
\`QUEUED\`, \`CRAG_EVAL\`, \`CODING\`, \`TESTING\`, \`COMPLETED\`,
\`FAILED\` \|

\| \`crag_status\` \| \`VARCHAR(32)\` \| \`NOT NULL, DEFAULT 'PENDING'\`
\| \`SUFFICIENT\`, \`AUGMENTED\` \|

\| \`retry_count\` \| \`INTEGER\` \| \`NOT NULL, DEFAULT 0\` \| Number
of \`pytest\` sandbox retries (\`0..3\`) \|

\| \`generated_artifacts\` \| \`JSONB\` \| \`NOT NULL, DEFAULT '{}'\` \|
Map of relative file paths to generated code \|

\| \`sandbox_logs\` \| \`TEXT\` \| \`NULLABLE\` \| Captured \`pytest\`
stdout/stderr \|

\| \`pr_url\` \| \`VARCHAR(512)\` \| \`NULLABLE\` \| Resulting GitHub
Pull Request URL \|

\| \`created_at\` \| \`TIMESTAMPTZ\` \| \`NOT NULL, DEFAULT now()\` \|
Job start time \|

---

\## 3. Relational ER Diagram

\`\`\`mermaid

erDiagram

USERS \|\|--o{ REPOSITORIES : owns

REPOSITORIES \|\|--o{ SEAM_CLUSTERS : contains

REPOSITORIES \|\|--o{ MIGRATION_JOBS : runs

SEAM_CLUSTERS \|\|--o{ MIGRATION_JOBS : targets

## **4. Neo4j Knowledge Graph Schema**

### **4.1 Node Labels & Properties**

All nodes carry the base label :CodeNode plus a specific label (:File,
:Class, :Function, :APIEndpoint, :DBTable):

-   id: STRING ("{repo_id}:{qualified_name}", Unique Constraint)

-   repo_id: STRING (Indexed for tenant isolation)

-   name: STRING (Short symbol name, e.g., execute)

-   qualified_name: STRING (e.g.,
    > payments.processor.PaymentProcessor.execute)

-   file_path: STRING

-   start_line: INTEGER, end_line: INTEGER

-   docstring: STRING

-   source_code: STRING

-   community_id: INTEGER (Written by Agent 2 Louvain clustering)

-   embedding: LIST\<FLOAT\> (1536-d vector index on :Function)

### **4.2 Relationship Types**

-   (:File)-\[:CONTAINS\]-\>(:Class \| :Function)

-   (:Class)-\[:CONTAINS\]-\>(:Function)

-   (:Function)-\[:CALLS {line: INTEGER}\]-\>(:Function)

-   (:Class)-\[:INHERITS\]-\>(:Class)

-   (:Function)-\[:READS_FROM\]-\>(:DBTable)

-   (:Function)-\[:WRITES_TO\]-\>(:DBTable)

-   (:APIEndpoint)-\[:ROUTES_TO\]-\>(:Function)

### **4.3 Neo4j Constraints & Indexes**

Cypher

CREATE CONSTRAINT code_node_id_unique IF NOT EXISTS FOR (n:CodeNode)
REQUIRE n.id IS UNIQUE;

CREATE INDEX code_node_repo_idx IF NOT EXISTS FOR (n:CodeNode) ON
(n.repo_id);

CREATE INDEX code_node_qname_idx IF NOT EXISTS FOR (n:CodeNode) ON
(n.repo_id, n.qualified_name);

CREATE VECTOR INDEX function_embedding_idx IF NOT EXISTS FOR
(f:Function) ON (f.embedding)

OPTIONS {indexConfig: {\`vector.dimensions\`: 1536,
\`vector.similarity_function\`: 'cosine'}};

## **5. Sensitive Data, Migrations & Seed Strategy**

-   **Sensitive Data:** encrypted_github_pat is encrypted using
    > cryptography.fernet.Fernet derived from ENCRYPTION_SECRET_KEY
    > before saving to PostgreSQL.

-   **Migrations:** Managed via alembic upgrade head on backend startup;
    > Neo4j constraints are applied idempotently during FastAPI lifespan
    > startup.

-   **Seed Strategy:** Includes a built-in sample_monolith seeder so
    > developers and recruiters can test full GraphRAG + CRAG flows
    > immediately after docker compose up.

---

\## 5. \`API.md\`

\`\`\`markdown

\# API & Integration Specification: C.O.R.E.

\## 1. Base Conventions & Authentication

\- \*\*Base URL:\*\* \`/api/v1\`

\- \*\*Authentication:\*\* Every endpoint (except \`/api/v1/health\`)
requires \`Authorization: Bearer \<CLERK_SESSION_JWT\>\`. FastAPI
verifies the token against Clerk's JWKS endpoint and resolves the
internal \`user_id\`.

\- \*\*Standard Error Format:\*\*

\`\`\`json

{

"error": {

"code": "RESOURCE_NOT_FOUND",

"message": "Repository not found or access denied.",

"details": {}

}

}

## **2. Core Endpoints**

### **2.1 System & Auth**

-   **GET /api/v1/health** — Checks PostgreSQL, Neo4j, and Redis
    > connectivity. Returns 200 OK.

-   **PUT /api/v1/settings/github-token** — Encrypts and saves a user's
    > GitHub PAT.

### **2.2 Repositories & AST Ingestion (Agent 1)**

-   **POST /api/v1/repos**

    -   **Purpose:** Register and trigger AST + GraphRAG ingestion for a
        > repository (or use_sample_monolith: true).

    -   **Request Body:**

    -   JSON

{

"name": "acme/legacy-shop",

"repo_url":
"\[https://github.com/acme/legacy-shop.git\](https://github.com/acme/legacy-shop.git)",

"default_branch": "main",

"use_sample_monolith": false

}

-   

-   

-   **Response (202 Accepted):** { "repo_id": "uuid", "status":
    > "INGESTING" }

<!-- -->

-   **GET /api/v1/repos** — Lists all repositories owned by the
    > authenticated user.

-   **GET /api/v1/repos/{repo_id}/graph** — Returns React Flow
    > compatible nodes and edges arrays for the repository, including
    > community_id and shared DB table metadata.

### **2.3 Boundary Mapper & Microservice Seams (Agent 2)**

-   **POST /api/v1/repos/{repo_id}/seams/analyze**

    -   **Purpose:** Runs Louvain community detection, identifies shared
        > DB table bottlenecks, and synthesizes microservice extraction
        > candidates.

    -   **Response (200 OK):**

    -   JSON

{

"seams": \[

{

"id": "uuid",

"community_id": 2,

"suggested_name": "payment-service",

"cohesion_score": 0.87,

"shared_db_tables": \["orders_table"\],

"node_ids": \["payments.processor.PaymentProcessor.execute"\]

}

\]

}

-   
-   

### **2.4 Blast Radius Impact Predictor (Agent 3)**

-   **POST /api/v1/repos/{repo_id}/impact**

    -   **Purpose:** Traces 1-to-5 hop callers and database dependencies
        > for a target symbol.

    -   **Request Body:**

    -   JSON

{

"target_qualified_name": "payments.processor.PaymentProcessor.execute",

"max_hops": 3,

"proposed_change_summary": "Add mandatory currency_code parameter"

}

-   

-   

-   **Response (200 OK):**

-   JSON

{

"target": "payments.processor.PaymentProcessor.execute",

"risk_score": 78,

"affected_nodes": \["orders.checkout.place_order",
"api.routes.checkout_endpoint"\],

"affected_edges": \[{"source": "orders.checkout.place_order", "target":
"payments.processor.PaymentProcessor.execute", "hops": 1}\],

"affected_db_tables": \["ledger_entries"\],

"impact_summary": "High risk: Breaking signature affects 1 public API
route and 2 upstream modules."

}

-   
-   

### **2.5 Migration Orchestrator, CRAG Sandbox & GitHub PR (Agent 4)**

-   **POST /api/v1/repos/{repo_id}/migrations**

    -   **Purpose:** Starts the LangGraph workflow (CRAG evaluation -\>
        > Code generation -\> Pytest sandbox verification -\> Optional
        > GitHub PR).

    -   **Request Body:** { "seam_id": "uuid", "create_github_pr": true
        > }

    -   **Response (202 Accepted):** { "job_id": "uuid", "status":
        > "QUEUED" }

-   **GET /api/v1/repos/{repo_id}/migrations/{job_id}/stream**

    -   **Purpose:** Server-Sent Events (SSE) stream emitting real-time
        > state updates (AGENT_START, CRAG_DECISION, SANDBOX_ITERATION,
        > ARTIFACTS_READY, PR_CREATED).

-   **POST /api/v1/repos/{repo_id}/migrations/{job_id}/pr**

    -   **Purpose:** Manually triggers GitHub branch push and Pull
        > Request creation for a completed migration job.

## **3. Rate Limiting & Failure Handling**

-   **Rate Limits (Redis Sliding Window):**

    -   Graph & Read endpoints: 60 requests / minute / user

    -   Ingestion & LangGraph Migration endpoints: 10 requests / minute
        > / user

-   **External API Resilience:** LLM calls use exponential backoff
    > (max_retries=3) and structured output parsing
    > (with_structured_output). GitHub API calls enforce a 15-second
    > timeout and surface clean error codes if token scopes are
    > insufficient.

## **4. Required Environment Variables**

Ini, TOML

\# Git & Project Identity

GIT_AUTHOR_NAME=devasya0711

GIT_AUTHOR_EMAIL=devasyakachru.007@gmail.com

\# Databases

DATABASE_URL=postgresql+asyncpg://core_user:core_pass@postgres:5432/core_db

NEO4J_URI=bolt://neo4j:7687

NEO4J_USER=neo4j

NEO4J_PASSWORD=core_neo4j_pass

REDIS_URL=redis://redis:6379/0

\# Authentication (Clerk)

NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test\_...

CLERK_SECRET_KEY=sk_test\_...

CLERK_JWKS_URL=https://\<your-clerk-domain\>/.well-known/jwks.json

\# AI & Security

OPENAI_API_KEY=sk-...

ENCRYPTION_SECRET_KEY=\<32-byte-base64-fernet-key\>

---

\## 6. \`AGENTS.md\`

\`\`\`markdown

\# AI Coding Agent Rules (\`AGENTS.md\`)

You are an autonomous Senior Full-Stack & AI Systems Engineer building
\*\*C.O.R.E. (Codebase Orchestration & Refactoring Engine)\*\*. You must
follow these rules strictly on every task.

\## 1. Source of Truth Hierarchy

Before creating or modifying any file, inspect the existing codebase and
adhere strictly to:

1\. \`PRD.md\` (Scope & functional requirements)

2\. \`ARCHITECTURE.md\` (System design, folder structure, LangGraph
flow)

3\. \`DATABASE.md\` (PostgreSQL tables & Neo4j node/edge schema)

4\. \`API.md\` (Endpoint paths, payloads, status codes)

5\. \`DESIGN.md\` (Dark-mode industrial design tokens, typography, React
Flow UX)

\## 2. Git & Change Discipline (MANDATORY)

\- \*\*Repository:\*\* \`https://github.com/devasya0711/C.O.R.E..git\`

\- \*\*Git Author Identity:\*\* Always ensure local git config is set
to:

\- \`git config user.name "devasya0711"\`

\- \`git config user.email "devasyakachru.007@gmail.com"\`

\- \*\*Atomic Verified Commits:\*\* After implementing and testing each
feature or sub-phase:

1\. Run linters and relevant tests (\`pytest\` for backend, \`npm run
build\` / \`tsc\` for frontend).

2\. Only when tests pass, stage changes (\`git add .\`), commit with a
conventional commit message (\`feat:\`, \`fix:\`, \`chore:\`,
\`test:\`), and push (\`git push origin main\`).

\## 3. Code Quality & Type Safety

\- \*\*Python Backend:\*\* Python 3.12+, strict type hints on all
functions, Pydantic v2 models for all I/O, async SQLAlchemy 2.0, and
\`ruff\` for linting/formatting.

\- \*\*TypeScript Frontend:\*\* Strict TypeScript (\`noImplicitAny:
true\`). Never use \`any\`. Define shared interfaces in
\`frontend/src/types/\`.

\- \*\*No Mock/Placeholder Logic in Production Paths:\*\* Do not leave
\`# TODO: implement later\` in core agents. AST parsing, Neo4j Cypher
queries, Louvain clustering, and the \`pytest\` sandbox runner must
execute real logic.

\## 4. Security & Secrets Rules

\- \*\*Zero Hardcoded Secrets:\*\* Never commit API keys, Clerk secrets,
or database passwords. Read all secrets via \`pydantic-settings\`
(\`backend/app/core/config.py\`) or \`process.env\`.

\- \*\*Parameterized Cypher Only:\*\* Never interpolate user strings
directly into Neo4j Cypher queries. Always pass parameters
(\`session.run(query, repo_id=str(repo_id), ...)\`).

\- \*\*Server-Side Auth Enforcement:\*\* Every FastAPI route must inject
the \`get_current_user\` dependency and verify \`repository.user_id ==
current_user.id\`.

\## 5. Testing Requirements

\- Every backend agent and API endpoint must have corresponding
\`pytest\` tests in \`backend/tests/\`.

\- Use \`sample_monolith/\` as the deterministic fixture repository to
verify AST parsing, seam detection, blast radius accuracy, and CRAG
sandbox testing.

## **7. Implementation Plan**

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
    > sample_monolith/\*\*, backend/app/main.py, frontend/package.json.

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

-   **Files Affected:** backend/app/db/\*\*,
    > backend/app/core/security.py, backend/alembic/\*\*,
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

    2.  Build Architecture Graph Studio (/repos/\[repoId\]/graph) with
        > custom React Flow nodes, Seam Cluster coloring toggle, and
        > animated red Blast Radius path highlighting.

    3.  Build Migration Orchestrator
        > (/repos/\[repoId\]/migrations/\[jobId\]) with live SSE
        > stepper, CRAG retry logs, and code diff viewer.

-   **Files Affected:** frontend/src/app/\*\*,
    > frontend/src/components/\*\*.

-   **Tests Required:** Frontend production build (npm run build) and
    > Playwright E2E smoke test.

-   **Completion Criteria:** Full application runs via Docker Compose,
    > passes CI, and is pushed to
    > \[https://github.com/devasya0711/C.O.R.E..git\](https://github.com/devasya0711/C.O.R.E..git).

**Recommended First Task:** Execute **Prompt 8 (Project Setup Prompt)**
below to initialize the repository, set your Git identity (devasya0711),
build sample_monolith, and bring up the Docker Compose stack.

## **8–20. Tailored Execution Prompts for C.O.R.E.**

Copy and paste each prompt below into your coding agent as you progress
through the phases.

### **8. Project Setup Prompt**

Markdown

You are a senior full-stack engineer responsible for establishing
production-ready project foundations for C.O.R.E. (Codebase
Orchestration & Refactoring Engine).

CONTEXT:

Read PRD.md, DESIGN.md, ARCHITECTURE.md, DATABASE.md, API.md, and
AGENTS.md before making changes.

TASK:

Initialize the C.O.R.E. monorepo according to ARCHITECTURE.md and Phase
1 of the Implementation Plan:

1\. Configure local Git identity:

\- \`git config user.name "devasya0711"\`

\- \`git config user.email "devasyakachru.007@gmail.com"\`

\- Ensure remote \`origin\` points to
\`https://github.com/devasya0711/C.O.R.E..git\`

2\. Set up \`docker-compose.yml\` provisioning PostgreSQL 16, Neo4j 5
(with APOC & GDS plugins enabled), Redis 7, \`backend\` (FastAPI on
Python 3.12), and \`frontend\` (Next.js 15 App Router with TypeScript,
Tailwind CSS, Shadcn UI, and \`@xyflow/react\`).

3\. Create \`.env.example\` with all variables defined in API.md, and a
strict \`.gitignore\`.

4\. Create \`sample_monolith/\` containing a realistic 6-file Python
e-commerce application (\`auth.py\`, \`orders.py\`, \`payments.py\`,
\`inventory.py\`, \`models.py\`, \`routes.py\`) with cross-module
function calls, SQLAlchemy table definitions, and a working
\`tests/test_monolith.py\` suite.

5\. Configure \`.github/workflows/ci.yml\` to run Ruff linting, Pytest,
and Frontend type-checking on every push.

6\. Implement \`GET /api/v1/health\` in FastAPI and the base industrial
dark-mode layout (\`#090A0F\` canvas, Inter + JetBrains Mono fonts) in
Next.js.

RULES:

Follow ARCHITECTURE.md and AGENTS.md strictly. Never hardcode secrets.
Verify the setup runs, run tests, and commit and push to GitHub (\`git
push origin main\`).

### **9. Feature Development Prompt (Reusable Template)**

Markdown

You are a senior full-stack engineer working inside the C.O.R.E.
production codebase.

CONTEXT:

Follow PRD.md, DESIGN.md, ARCHITECTURE.md, DATABASE.md, API.md, and
AGENTS.md. Implement only this approved task:

\[INSERT PHASE / TASK — e.g., Phase 3: Agent 1 Static Analyzer & Hybrid
GraphRAG Ingestion OR Phase 4: Agent 2 Boundary Mapper & Agent 3 Impact
Predictor\]

TASK:

First inspect the relevant existing code and dependencies. Briefly state
your implementation approach, then implement the feature completely:

\- Follow the exact Neo4j node/edge schema in DATABASE.md and endpoint
contracts in API.md

\- Follow the dark-mode industrial design tokens in DESIGN.md

\- Handle loading, empty, and error states cleanly

\- Validate all inputs with Pydantic v2 / TypeScript and enforce Clerk
user ownership

\- Add unit/integration tests in \`backend/tests/\` using
\`sample_monolith/\`

RULES:

Do not rewrite unrelated code or introduce unnecessary dependencies.
Parameterize all Cypher queries. Once all tests pass, commit and push
your changes to \`https://github.com/devasya0711/C.O.R.E..git\` as
\`devasya0711\`.

### **10. Database & Backend Connection Prompt**

Markdown

You are a senior backend engineer and database specialist experienced in
production systems.

CONTEXT:

Follow DATABASE.md, ARCHITECTURE.md, API.md, and AGENTS.md as the source
of truth.

TASK:

Connect and implement C.O.R.E.'s PostgreSQL, Neo4j, and Redis data layer
(Phase 2):

1\. Configure SQLAlchemy 2.0 Async engine
(\`backend/app/db/session.py\`) and Alembic migrations for \`users\`,
\`repositories\`, \`seam_clusters\`, and \`migration_jobs\`.

2\. Configure the async Neo4j driver (\`backend/app/db/neo4j.py\`) and
implement idempotent startup initialization for uniqueness constraints
(\`code_node_id_unique\`), composite indexes (\`repo_id\`,
\`qualified_name\`), and the 1536-d vector index
(\`function_embedding_idx\`).

3\. Configure the async Redis client (\`backend/app/db/redis.py\`) for
Blast Radius caching (\`TTL=300s\`), rate limiting, and SSE pub/sub.

4\. Implement Fernet AES encryption/decryption in
\`backend/app/core/security.py\` for \`encrypted_github_pat\`.

5\. Add \`pytest\` tests verifying PostgreSQL CRUD, Neo4j constraint
creation, and Redis connectivity.

RULES:

Never hardcode credentials. Parameterize all Cypher and SQL queries.
Enforce tenant isolation (\`user_id\` and \`repo_id\`) on every query.
Test, commit, and push to GitHub.

### **11. Authentication & Authorization Prompt**

Markdown

You are a senior security-focused full-stack engineer specializing in
production authentication systems.

CONTEXT:

Follow PRD.md, ARCHITECTURE.md, DATABASE.md, API.md, and AGENTS.md.

TASK:

Implement end-to-end Clerk Authentication and Authorization for
C.O.R.E.:

1\. In \`frontend/\`, configure \`@clerk/nextjs\` middleware
(\`src/middleware.ts\`) protecting \`/dashboard(.\*)\`, \`/repos(.\*)\`,
and \`/settings(.\*)\`, plus styled \`/sign-in\` and \`/sign-up\` routes
matching DESIGN.md.

2\. In \`backend/app/core/auth.py\`, implement JWKS token verification
(\`get_current_user\`) that validates the \`Authorization: Bearer
\<token\>\` header against \`CLERK_JWKS_URL\` and
auto-provisions/fetches the corresponding \`User\` row in PostgreSQL.

3\. Enforce resource ownership across all repository, graph, seam,
impact, and migration endpoints so users can only access repositories
where \`repository.user_id == current_user.id\`.

4\. Add backend tests testing \`401 Unauthorized\` (missing/invalid
token) and \`403 Forbidden\` (cross-tenant access attempt).

RULES:

Never trust client-side user IDs; always derive identity from the
verified Clerk JWT server-side. Test, commit, and push to GitHub.

### **12. MCP Connection Prompt**

Markdown

You are a senior AI engineer experienced with MCP servers and AI coding
agents.

CONTEXT:

The C.O.R.E. development environment requires connecting to the
following MCP capability:

\[MCP SERVER: e.g., GitHub MCP Server
(\`@modelcontextprotocol/server-github\`) OR Neo4j Cypher MCP Server
(\`mcp-neo4j-cypher\`)\]

Follow AGENTS.md and the existing project architecture.

TASK:

Connect and configure the MCP server for the coding environment:

1\. Determine the exact tools exposed by the MCP server and restrict
usage to least-privilege operations required by C.O.R.E. (e.g.,
inspecting Neo4j schema/nodes or managing PRs on
\`devasya0711/C.O.R.E.\`).

2\. Configure the MCP settings using environment variables without
hardcoding any tokens in tracked files.

3\. Verify the MCP connection by executing a read-only diagnostic call
and report the status, available tools, and any manual approval steps.

### **13. External API / Service Integration Prompt**

Markdown

You are a senior integration engineer experienced in building reliable
production integrations.

CONTEXT:

Follow API.md, ARCHITECTURE.md, DATABASE.md, and AGENTS.md.

TASK:

Integrate the external services required for Phase 5 of C.O.R.E.:

1\. \*\*LLM & Embedding Service
(\`backend/app/services/llm_service.py\`):\*\* Implement
structured-output LLM calls (for CRAG grading, Seam naming, and Refactor
Coder synthesis) and batch embedding generation with exponential
backoff, timeout handling (\`30s\`), and token usage logging.

2\. \*\*GitHub API Service
(\`backend/app/services/github_service.py\`):\*\* Using \`PyGithub\` and
the user's decrypted PAT, implement repository cloning, branch creation
(\`refactor/extract-{seam_name}\`), multi-file commits, and Pull Request
creation complete with an embedded Mermaid dependency graph and Blast
Radius risk table.

3\. Write unit and mocked integration tests covering success, rate-limit
(\`429\`), and bad-credential (\`401\`) failure states.

RULES:

Never log raw GitHub tokens or API keys. Ensure retries are idempotent.
Test, commit, and push to GitHub.

### **14. Testing & QA Prompt**

Markdown

You are a senior QA engineer and test automation specialist responsible
for production software quality.

CONTEXT:

Read PRD.md, API.md, ARCHITECTURE.md, and the current C.O.R.E.
implementation before testing.

TASK:

Execute a comprehensive QA suite across C.O.R.E.:

1\. \*\*Unit & Integration Tests (\`pytest\`):\*\*

\- AST parsing accuracy on \`sample_monolith\` (verify all \`File\`,
\`Class\`, \`Function\`, \`DBTable\` nodes and \`CALLS\` / \`WRITES_TO\`
edges).

\- Louvain seam clustering determinism and shared database table
detection.

\- Multi-hop Blast Radius traversal (\`1..5\` hops) and risk score
calculation.

\- LangGraph CRAG Evaluator fallback trigger and \`pytest\` sandbox
self-correction retry loop (test both passing code and intentionally
broken imports that self-heal on retry \#2).

\- Cross-tenant authorization checks (\`401\` / \`403\`).

2\. \*\*Frontend E2E & Build Verification:\*\*

\- Verify \`npm run build\` and TypeScript compilation pass with zero
errors.

\- Verify empty, loading, error, and interactive React Flow graph
states.

RULES:

Do not modify production logic just to bypass failing tests—fix the
underlying root cause. Commit all test suites and push to GitHub.

### **15. Debugging / Root Cause Prompt**

Markdown

You are a senior debugging engineer responsible for diagnosing
production-quality software issues in C.O.R.E.

CONTEXT:

Follow ARCHITECTURE.md, DATABASE.md, API.md, and AGENTS.md rules.

PROBLEM:

\[PASTE ERROR / TRACEBACK / NEO4J CYPHER ERROR / DOCKER LOG / SCREENSHOT
HERE\]

TASK:

1\. Reproduce or trace the failure across the FastAPI backend, LangGraph
state machine, Neo4j Cypher query, or Next.js frontend.

2\. Identify and explain the exact root cause.

3\. Implement the smallest, cleanest fix that preserves architectural
boundaries.

4\. Add a regression test ensuring this bug cannot recur, run the test
suite, and commit and push the fix to GitHub.

### **16. Security Audit & Hardening Prompt**

Markdown

You are a senior application security engineer specializing in
production web applications.

CONTEXT:

Audit the C.O.R.E. codebase against PRD.md, ARCHITECTURE.md,
DATABASE.md, API.md, and AGENTS.md.

TASK:

Perform a comprehensive security audit and remediate any findings:

1\. \*\*Cypher & SQL Injection:\*\* Verify every Neo4j query uses
parameterized bindings (\`$repo_id\`, \`$qualified_name\`) and hop depth
is strictly validated as an integer \`1..5\`.

2\. \*\*Sandbox Escape Prevention:\*\* Verify \`sandbox_runner.py\`
strips sensitive host environment variables (\`OPENAI_API_KEY\`,
\`DATABASE_URL\`, \`CLERK_SECRET_KEY\`, \`ENCRYPTION_SECRET_KEY\`),
enforces a strict 30-second execution timeout, and cleans up temporary
directories.

3\. \*\*Authentication & Tenant Isolation:\*\* Verify every API route
validates the Clerk JWT and checks \`repository.user_id ==
current_user.id\`.

4\. \*\*Secrets & Logging:\*\* Confirm \`encrypted_github_pat\` is never
returned in API responses or written to logs.

5\. \*\*Rate Limiting & CORS:\*\* Verify Redis sliding-window rate
limits and strict CORS origins are active.

OUTPUT:

Report findings by severity, apply fixes, run all tests, and commit and
push to GitHub.

### **17. Code Review & Refactoring Prompt**

Markdown

You are a senior software engineer reviewing the C.O.R.E. codebase
before release.

CONTEXT:

Review the current implementation against PRD.md, DESIGN.md,
ARCHITECTURE.md, DATABASE.md, API.md, and AGENTS.md.

TASK:

Perform a thorough code review and clean up technical debt:

1\. Check for any remaining mock data or stubbed methods in the 4 agents
(\`static_analyzer\`, \`boundary_mapper\`, \`impact_predictor\`,
\`refactor_coder\`) or CRAG evaluator.

2\. Verify strict Pydantic v2 schemas in \`backend/app/schemas/\` and
strict TypeScript types (zero \`any\`) in \`frontend/src/\`.

3\. Verify UI adherence to DESIGN.md (\`#090A0F\` obsidian canvas,
semantic node colors, \`JetBrains Mono\` technical labels, smooth React
Flow Blast Radius edge highlighting).

4\. Remove dead code, unused imports, and duplicate utilities.

OUTPUT:

Report findings, apply safe refactorings, verify all tests pass, and
commit and push to GitHub.

### **18. Deployment & Production Setup Prompt**

Markdown

You are a senior DevOps engineer experienced in deploying secure
production applications.

CONTEXT:

Follow ARCHITECTURE.md, API.md, DATABASE.md, and AGENTS.md.

TASK:

Prepare C.O.R.E. for production deployment and recruiter evaluation:

1\. Verify multi-stage production \`Dockerfile\` builds for both
\`backend/\` and \`frontend/\`.

2\. Verify \`docker-compose.yml\` starts PostgreSQL, Neo4j (with APOC &
GDS), Redis, Backend (running \`alembic upgrade head\` on entrypoint),
and Frontend cleanly from a cold start.

3\. Verify \`.github/workflows/ci.yml\` passes all linting,
unit/integration tests, and Docker build checks.

4\. Generate an executive-grade \`README.md\` with:

\- Mermaid system architecture diagram of the 4 Agents + CRAG Sandbox +
Neo4j Hybrid GraphRAG.

\- Feature walkthrough (AST Ingestion, Louvain Seam Clustering, 5-Hop
Blast Radius Simulator, Self-Healing CRAG PR Engine).

\- 1-command quickstart (\`docker compose up --build\`).

OUTPUT:

Verify the full stack builds and runs cleanly, then commit and push to
\`https://github.com/devasya0711/C.O.R.E..git\`.

### **19. Production Monitoring & Maintenance Prompt**

Markdown

You are a senior SRE responsible for keeping C.O.R.E. reliable and
observable in production.

CONTEXT:

Review the C.O.R.E. codebase and ARCHITECTURE.md before making changes.

TASK:

Implement structured observability and operational health checks:

1\. Configure \`structlog\` JSON logging in
\`backend/app/core/logging.py\` with request correlation IDs
(\`X-Request-ID\`), agent execution latency metrics, CRAG retry counts,
and redacted sensitive fields.

2\. Enhance \`GET /api/v1/health\` to report granular component
latencies (\`postgres_ms\`, \`neo4j_ms\`, \`redis_ms\`) and return \`503
Service Unavailable\` if any critical store is unreachable.

3\. Add Neo4j and PostgreSQL backup/restore helper scripts in
\`scripts/\` and document recovery procedures.

OUTPUT:

Implement, test, and commit and push the observability improvements to
GitHub.

### **20. Final Production Audit Prompt**

Markdown

You are a senior engineering lead responsible for approving C.O.R.E. for
production launch and portfolio showcase.

CONTEXT:

Audit the complete repository against PRD.md, DESIGN.md,
ARCHITECTURE.md, DATABASE.md, API.md, and AGENTS.md.

TASK:

Perform an end-to-end production readiness verification:

1\. Boot the entire stack and run the \`sample_monolith\` end-to-end
journey:

\- Ingest \`sample_monolith\` via Agent 1 -\> Verify Neo4j node/edge
counts and embeddings.

\- Run Agent 2 Seam Analysis -\> Verify Louvain clusters and shared DB
table warnings.

\- Run Agent 3 Blast Radius on \`PaymentProcessor.execute\` -\> Verify
multi-hop callers and risk score.

\- Trigger Agent 4 + CRAG Migration -\> Verify \`pytest\` sandbox
verification and PR payload generation.

2\. Verify all unit, integration, and frontend build checks pass.

3\. Verify \`git log\` confirms clean commits authored by \`devasya0711
\<devasyakachru.007@gmail.com\>\` pushed to
\`https://github.com/devasya0711/C.O.R.E..git\`.

OUTPUT:

Provide the launch verdict (\`READY\`, \`READY WITH WARNINGS\`, or \`NOT
READY\`), test results summary, and final verification checklist.
