# C.O.R.E. Build Plan

## Product summary

C.O.R.E. is a multi-agent GraphRAG SaaS workspace for safely extracting
microservices from legacy Python monoliths. It deterministically parses a
repository with Tree-sitter into a tenant-partitioned Neo4j code and vector
graph, uses Louvain community detection to expose low-coupling seams, traces
one-to-five-hop blast radius and risk before a change, and runs generated
refactors through a constrained pytest/CRAG self-correction loop before
creating a GitHub pull request. PostgreSQL stores workspace and job metadata,
Redis handles cache/rate limits/streaming, FastAPI and LangGraph provide the
orchestration API, and a Next.js React Flow studio provides the industrial
dark-mode interface.

## Source and decision log

The source document was found at `Project Specs.docx` (the request called it
`Project_Specs.docx`). It was converted with Pandoc to `docs/SPEC_FULL.md`
before implementation. The embedded documents were split into the files in
`docs/` and the embedded agent rules were copied to the repository root as
`AGENTS.md`.

The following decisions resolve explicit ambiguities, conflicts, or approval
markers without blocking implementation:

1. **[ASSUMPTION-1] Python first.** MVP parsing is implemented for Python with
   interfaces that can accept Java/TypeScript parsers later. This follows the
   PRD and keeps the deterministic sample flow in scope.
2. **[ASSUMPTION-2] Repository sources.** Public GitHub URLs, private GitHub
   URLs using a stored PAT/GitHub App credential, and the built-in
   `local://sample_monolith` source are supported first. The PRD also mentions
   `.zip` uploads up to 100 MB, but API.md defines no upload endpoint or
   payload; zip support is therefore an explicitly documented follow-up, not
   an invented route.
3. **[APPROVAL REQUIRED-1] LLM provider.** No provider approval or secret was
   supplied in the workspace. OpenAI is the configurable primary default
   (`gpt-4o` and `text-embedding-3-small`, as named by the PRD), with provider
   interfaces and a deterministic offline path for tests and local graph
   ingestion when `OPENAI_API_KEY` is absent. Production calls never embed a
   key in source. Switching to Gemini is configuration work, not a schema
   change.
4. **FR-1 naming versus DATABASE.md.** FR-1 calls the concepts
   `API_Endpoint` and `DB_Table`; DATABASE.md is more specific and defines
   Neo4j labels `APIEndpoint` and `DBTable`. The database labels are canonical;
   API names remain user-facing terminology where appropriate.
5. **Relationship scope.** FR-1 lists the extraction relationships but
   DATABASE.md additionally requires `CONTAINS` and `ROUTES_TO`. All
   DATABASE.md node labels and relationships are implemented, including the
   additional relationships.
6. **IngestionJob omission.** The architecture flow and PRD data requirements
   refer to `ingestion_jobs`, while DATABASE.md's table detail omits it. The
   ingestion job is required for status/progress and is added with the fields
   needed by the API and SSE event model; this is a schema completion, not a
   replacement of any specified table.
7. **Health status conflict.** API.md says health returns 200; Prompt 19 says
   to return 503 when a critical store is unreachable. The implementation
   returns 200 only when all required stores are healthy and 503 with the
   standard error/latency payload when one is unavailable.
8. **Graph Data Science availability.** Docker provisions Neo4j with APOC and
   GDS configuration. Boundary Mapper first attempts GDS Louvain and uses the
   specified NetworkX Louvain fallback when GDS is unavailable; this makes
   local CI deterministic without hiding a production GDS failure.
9. **Ingestion progress endpoint.** PRD/architecture require progress events,
   but API.md specifies only the migration SSE endpoint. Progress is emitted
   to Redis and persisted on the ingestion job; no undocumented public route
   is added until an API contract is approved. The frontend uses the
   documented contract and repository status while the internal stream is
   available to the worker.
10. **API URL example.** The malformed Markdown link in the repository-create
    example is treated as the plain string `https://github.com/...git`.
11. **Sandbox isolation.** The preferred runner uses a temporary isolated
    container with no network; a local subprocess mode is available only for
    environments without Docker and still strips secrets, limits time, and
    cleans its directory. The limitation is surfaced in deployment/audit
    results rather than represented as container isolation.
12. **MCP prompt placeholder.** Prompt 12 provides examples rather than a
    selected MCP server. No MCP server is added without an actual endpoint or
    token; the required least-privilege configuration will be documented as a
    deployment-time integration when credentials are supplied.
13. **External credentials and GitHub push.** The workspace contains no
    Clerk, OpenAI, GitHub, or hosted-database credentials. All local logic,
    tests, mocked service boundaries, and Docker configuration will be built
    without them. A push is attempted after each verified commit; an
    authentication failure is recorded as an environment blocker rather than
    faked as success.
14. **Performance evidence.** The 500 ms/90 second/30 second targets are
    acceptance targets. Local tests will measure the deterministic fixtures;
    full 10,000-node and hosted-service evidence is marked as environment
    dependent if the required services are unavailable.
15. **Generated artifact names.** Agent 4 follows the specified FastAPI
    service, Dockerfile, Strangler Fig proxy, and adapter outputs. It does not
    add database data migration automation because PRD explicitly places that
    out of scope.

## Batch sequence and acceptance criteria

The following batch list preserves the goals, affected files, tests, and
completion criteria from Section 7 of the specification. Batches 7–13 split
the requested Prompt 14–20 production pass into independently verifiable
commits.

### Batch 1 — Phase 1: Repository Initialization, Docker Infrastructure & CI/CD

- **Goal:** Configure Git identity, containerize PostgreSQL, Neo4j (APOC/GDS),
  Redis, Backend, and Frontend, and create the GitHub Actions CI pipeline.
- **Tasks:** Configure author and root ignore/env files; create Compose with
  healthchecks; scaffold FastAPI and Next.js; create the realistic six-module
  `sample_monolith` and pytest suite; create CI for Ruff, Pytest, TypeScript,
  and Docker builds.
- **Files:** `docker-compose.yml`, `.github/workflows/ci.yml`,
  `sample_monolith/**`, `backend/app/main.py`, `frontend/package.json`.
- **Tests:** `GET /api/v1/health` returns 200 OK; pytest runs cleanly on
  `sample_monolith`.
- **Completion:** All containers boot cleanly via `docker compose up -d` and
  the initial commit is pushed to GitHub.

**Batch 1 verification:**

- [x] Root `.gitignore`, `.env.example`, required Git identity, and remote are
  configured.
- [x] FastAPI `GET /api/v1/health` returns HTTP 200 with PostgreSQL, Neo4j, and
  Redis component statuses when services are not configured locally.
- [x] The six-module `sample_monolith` has deterministic checkout tests.
- [x] Ruff lint and format checks pass for `backend` and `sample_monolith`.
- [x] `PYTHONPATH=. pytest sample_monolith backend/tests` passes (5 tests).
- [x] Frontend `npm run typecheck` passes.
- [x] Frontend `npm run build` passes with Next.js standalone output.
- [x] `docker-compose.yml` and CI workflow are present and YAML-structured.
- [ ] `docker compose up -d` and image builds are not verifiable here because
  the Docker CLI/daemon is unavailable in this environment. GitHub Actions
  remains configured to run the Docker build check.
- [x] The initial Phase 1 commit `5dbce2b` was pushed from the user's
  SSH-authenticated WSL session; local `origin/main` points to that commit.

**Batch 1 CI repair (after the initial push):**

- Reproduced a clean frontend install failing at `npm ci`: the committed
  lockfile had incomplete optional Sharp platform-package entries. Existing
  local `node_modules` hid this during the original checks. Repaired the
  lockfile with `npm install --package-lock-only --ignore-scripts`, without
  upgrading the existing dependency versions.
- `npm run typecheck` now generates Next.js route declarations with
  `next typegen` before running `tsc --noEmit`; type checks are not disabled.
- The frontend Docker install now uses `npm ci` and requires the lockfile.
  `frontend/.dockerignore` prevents local `node_modules`, `.next`, environment
  files, and TypeScript caches from overwriting clean container dependencies.
- CI also runs the production frontend build and verifies that it leaves the
  lockfile unchanged. The existing clean `npm ci` step remains the regression
  check for the original install failure.
- Verified a fresh tracked-file snapshot (no `node_modules`, `.next`, or
  TypeScript cache): `npm ci`, `npm run typecheck`, and `npm run build` all
  passed on Node 22.23.3/npm 10.9.9. Lockfile contents before and after these
  commands have the same Git blob hash.
- Installed Python 3.12.15 in this runner and reran the backend CI checks in an
  isolated virtual environment: Ruff passed and pytest reported **5 passed**.
  Ruff formatting also passes. One upstream Starlette/httpx deprecation
  warning remains.
- `npm ci` reports 11 dependency advisories (3 moderate, 8 high); these were
  already present and were not hidden or addressed with an unreviewed forced
  upgrade. They remain security-audit work, not a passing security verdict.
- Remote Actions logs/check results are not accessible to this runner:
  `gh run list --repo devasya0711/C.O.R.E. --limit 3` requests authentication.
  The GitHub CLI is now installed, but the WSL login/SSH agent is not shared
  into the isolated runner. The repaired revision must be pushed and its
  GitHub checks confirmed before starting Batch 2. Docker image/boot
  verification remains unavailable locally because Docker is not installed.

### Batch 2 — Phase 2: Database Layer, Clerk Auth & Security Middleware

- **Goal:** Implement PostgreSQL models/migrations, Neo4j
  constraints/vector-index initialization, Clerk JWT verification, and Redis
  rate limiting.
- **Tasks:** Async SQLAlchemy models and Alembic migration; async Neo4j driver
  and idempotent indexes; Clerk `get_current_user` and Fernet encryption;
  Redis sliding-window limiter.
- **Files:** `backend/app/db/**`, `backend/app/core/security.py`,
  `backend/alembic/**`, `frontend/src/middleware.ts`.
- **Tests:** Auth rejection of missing/invalid JWTs, DB CRUD tests, Neo4j
  constraint verification, and Redis connectivity.
- **Completion:** Protected API routes enforce Clerk auth and migrations apply
  cleanly.

### Batch 3 — Phase 3: Agent 1 — Tree-Sitter AST Parser & Hybrid GraphRAG Ingestion

- **Goal:** Parse repositories into Neo4j nodes/edges and attach vector
  embeddings.
- **Tasks:** `StaticAnalyzerAgent` extracts File, Class, Function, APIEndpoint,
  and DBTable nodes plus CALLS, INHERITS, READS_FROM, and WRITES_TO edges;
  embedding pipeline batch-upserts into Neo4j; expose repository create/list/
  graph endpoints.
- **Files:** `backend/app/agents/static_analyzer.py`,
  `backend/app/services/neo4j_service.py`, `backend/app/api/v1/repos.py`.
- **Tests:** `test_static_analyzer.py` verifies exact node and CALLS edge counts
  on `sample_monolith`.
- **Completion:** Ingesting `sample_monolith` populates Neo4j and returns a
  valid React Flow graph JSON payload.

### Batch 4 — Phase 4: Agent 2 & Agent 3 — Boundary Mapper & Blast Radius Predictor

- **Goal:** Implement Louvain community seam clustering and one-to-five-hop
  Blast Radius analysis.
- **Tasks:** Boundary Mapper endpoint using Neo4j GDS (NetworkX fallback) and
  shared DB-table detection; Impact Predictor endpoint using bounded variable-
  length Cypher and a 0–100 risk score.
- **Files:** `backend/app/agents/boundary_mapper.py`,
  `backend/app/agents/impact_predictor.py`, `backend/app/api/v1/analysis.py`.
- **Tests:** `test_boundary_and_impact.py` verifies multi-hop caller recall on
  `PaymentProcessor.execute`.
- **Completion:** Both endpoints return deterministic graph clusters and
  multi-hop blast-radius paths.

### Batch 5 — Phase 5: Agent 4, CRAG Sandbox Loop & GitHub PR Automation

- **Goal:** Build the LangGraph state machine with CRAG context verification,
  code generation, pytest sandbox self-correction, and GitHub PR creation.
- **Tasks:** CRAG retrieval completeness/fallback; refactor artifact
  generation; isolated pytest runner with up to three retries; PyGithub branch,
  commit, and PR with Mermaid diagram; workflow wiring and migration SSE.
- **Files:** `backend/app/workflows/migration_graph.py`,
  `backend/app/agents/crag_evaluator.py`,
  `backend/app/agents/refactor_coder.py`,
  `backend/app/services/sandbox_runner.py`,
  `backend/app/services/github_service.py`.
- **Tests:** End-to-end payments-seam extraction from `sample_monolith` passes
  the sandbox pytest check.
- **Completion:** LangGraph extraction completes, sandbox tests pass, and
  state events stream.

### Batch 6 — Phase 6: Industrial Dark-Mode Frontend Studio & Final Polish

- **Goal:** Build the Next.js 15 UI following DESIGN.md, connect APIs, add E2E
  tests, and finalize documentation.
- **Tasks:** Dashboard, command palette, Settings; React Flow graph studio
  with node/seam/risk interactions; migration orchestrator with SSE stepper,
  CRAG retry logs, and diff viewer.
- **Files:** `frontend/src/app/**`, `frontend/src/components/**`.
- **Tests:** `npm run build` and a Playwright E2E smoke test.
- **Completion:** Full application runs via Docker Compose, passes CI, and is
  pushed to the specified repository.

### Batch 7 — Prompt 14: Testing & QA

- **Goal:** Execute the comprehensive QA suite across C.O.R.E.
- **Tests:** Pytest AST accuracy for all specified nodes and CALLS/WRITES_TO;
  deterministic Louvain and shared-table detection; one-to-five-hop impact and
  risk score; CRAG fallback and sandbox self-correction including a broken
  import healing on retry two; cross-tenant 401/403 checks; frontend build and
  TypeScript compilation; empty/loading/error/interactive graph states.
- **Completion:** Production logic is fixed at the root cause, all suites pass,
  and tests are committed and pushed.

### Batch 8 — Prompt 15: Debugging / Root Cause

- **Goal:** Reproduce any remaining FastAPI, LangGraph, Neo4j Cypher, Docker,
  or Next.js failure, explain its exact cause, and implement the smallest
  architectural fix.
- **Files/tests:** The failing subsystem plus a regression test; run the full
  relevant suite.
- **Completion:** The failure is reproducible or traced, regression-tested,
  verified, committed, and pushed. If no failure exists, record a clean
  diagnostic rather than inventing a change.

### Batch 9 — Prompt 16: Security Audit & Hardening

- **Goal:** Audit and remediate Cypher/SQL injection, sandbox escapes,
  authentication/tenant isolation, secret logging, rate limiting, and CORS.
- **Tests:** Parameter binding and hop bounds; stripped secret environment,
  30-second timeout, temp cleanup; JWT ownership checks; encrypted token
  response/log exclusion; Redis limits and strict origins.
- **Completion:** Findings are reported by severity, fixes are applied, all
  tests pass, and the remediation is committed and pushed.

### Batch 10 — Prompt 17: Code Review & Refactoring

- **Goal:** Review the implementation against all source documents and safely
  remove technical debt.
- **Checks:** No mock/stub methods in four agents or CRAG evaluator; strict
  Pydantic v2 and zero TypeScript `any`; DESIGN.md tokens and React Flow edge
  highlighting; dead code, unused imports, and duplicate utilities removed.
- **Completion:** Findings and safe refactorings are recorded, tests pass, and
  the review commit is pushed.

### Batch 11 — Prompt 18: Deployment & Production Setup

- **Goal:** Prepare C.O.R.E. for production deployment and recruiter
  evaluation.
- **Checks:** Multi-stage backend/frontend Dockerfiles; cold-start Compose with
  Alembic startup; CI lint/test/Docker checks; executive README with Mermaid
  four-agent/CRAG/Neo4j architecture, feature walkthrough, and
  `docker compose up --build` quickstart.
- **Completion:** The full stack builds and runs cleanly, then is committed and
  pushed.

### Batch 12 — Prompt 19: Production Monitoring & Maintenance

- **Goal:** Implement structured observability and operational health checks.
- **Tasks/tests:** Structlog JSON with `X-Request-ID`, latency, CRAG retry, and
  redaction fields; granular health latency fields and 503 behavior; Neo4j and
  PostgreSQL backup/restore helper scripts plus recovery documentation.
- **Completion:** Monitoring improvements are implemented, tested, committed,
  and pushed.

### Batch 13 — Prompt 20: Final Production Audit

- **Goal:** Approve C.O.R.E. for production launch and portfolio showcase.
- **Checks:** Boot the stack; ingest `sample_monolith` and verify graph counts/
  embeddings; run seams and shared-table warnings; run PaymentProcessor blast
  radius; trigger CRAG migration and verify pytest sandbox/PR payload; run all
  backend and frontend checks; verify clean author-attributed pushed history.
- **Completion:** Publish a launch verdict of `READY`, `READY WITH WARNINGS`,
  or `NOT READY`, with test results and the final verification checklist.

## Requirement-to-batch checklist

### User stories

- [ ] **US-1.1** Submit GitHub URL/branch and build an interactive Neo4j graph — **B3**
- [ ] **US-1.2** Stream/show cloning, AST, embedding, and graph-index progress — **B3/B5/B6**
- [ ] **US-2.1** Run Louvain and inspect cohesive seam clusters — **B4/B6**
- [ ] **US-2.2** Highlight cross-community `WRITES_TO`/`READS_FROM` shared tables — **B4/B6**
- [ ] **US-3.1** Simulate a signature change and trace one-to-five-hop callers/routes — **B4/B6**
- [ ] **US-3.2** Return an explained 0–100 Blast Radius Risk Score — **B4/B6**
- [ ] **US-4.1** Extract a seam into FastAPI service, Strangler Fig config, and adapter — **B5**
- [ ] **US-4.2** CRAG/pytest verify with up to three corrections before GitHub PR — **B5**

### Functional requirements

- [ ] **FR-1 Static Analyzer:** Tree-sitter File/Class/Function/APIEndpoint/DBTable nodes, CALLS/INHERITS/READS_FROM/WRITES_TO edges, function embeddings — **B3**
- [ ] **FR-2 Boundary Mapper:** GDS Louvain with NetworkX fallback, cohesion/coupling, shared-table conflicts — **B4**
- [ ] **FR-3 Impact Predictor:** bounded 1..5-hop Cypher, affected nodes/edges/routes, 0–100 risk — **B4**
- [ ] **FR-4 CRAG Evaluator:** completeness checks, unresolved import/type detection, full-file AST fallback — **B5**
- [ ] **FR-5 Refactor Coder & Sandbox:** service/Dockerfile/proxy/adapter artifacts, isolated pytest, stderr feedback, three retries — **B5**
- [ ] **FR-6 GitHub PR Engine:** `refactor/extract-{seam}`, verified commit, Mermaid graph, blast-radius table, PR — **B5**

### Non-functional requirements

- [ ] **Performance:** ≤500 ms five-hop graph query at 10,000 nodes and ≤90 s ingestion for 500 files — **B4/B7/B13**
- [ ] **Security:** Clerk JWT on API routes, AES-256/Fernet token encryption, parameterized Cypher, no-network isolated sandbox — **B2/B5/B9**
- [ ] **Reliability:** idempotent re-ingestion updates existing nodes without duplicates — **B3/B7/B13**

### API contract

- [ ] **GET `/api/v1/health`** connectivity check — **B1**, enhanced 503/latencies **B12**
- [ ] **PUT `/api/v1/settings/github-token`** encrypted PAT storage — **B2**
- [ ] **POST `/api/v1/repos`** register/trigger URL or sample ingestion, 202 — **B3**
- [ ] **GET `/api/v1/repos`** list authenticated owner's repositories — **B3**
- [ ] **GET `/api/v1/repos/{repo_id}/graph`** React Flow nodes/edges with community/shared-table data — **B3/B4**
- [ ] **POST `/api/v1/repos/{repo_id}/seams/analyze`** deterministic seam analysis — **B4**
- [ ] **POST `/api/v1/repos/{repo_id}/impact`** bounded impact request and risk response — **B4**
- [ ] **POST `/api/v1/repos/{repo_id}/migrations`** queue LangGraph migration, 202 — **B5**
- [ ] **GET `/api/v1/repos/{repo_id}/migrations/{job_id}/stream`** SSE state events — **B5**
- [ ] **POST `/api/v1/repos/{repo_id}/migrations/{job_id}/pr`** manual PR creation — **B5**
- [ ] Standard `{error: {code, message, details}}` failures and ownership-safe 404/403 behavior — **B2/B7/B9**
- [ ] Redis sliding windows: 60/min graph/read and 10/min ingestion/migration per user — **B2/B9**
- [ ] External resilience: LLM backoff max three, structured output, GitHub 15-second timeout — **B5/B9**

### Neo4j graph schema

- [ ] Base `CodeNode` label with tenant `repo_id` and required source/location properties — **B3**
- [ ] `File` node label — **B3**
- [ ] `Class` node label — **B3**
- [ ] `Function` node label with 1536-dimensional embedding index — **B2/B3**
- [ ] `APIEndpoint` node label — **B3**
- [ ] `DBTable` node label — **B3**
- [ ] `CONTAINS` File→Class/Function and Class→Function — **B3**
- [ ] `CALLS {line}` Function→Function — **B3**
- [ ] `INHERITS` Class→Class — **B3**
- [ ] `READS_FROM` Function→DBTable — **B3**
- [ ] `WRITES_TO` Function→DBTable — **B3**
- [ ] `ROUTES_TO` APIEndpoint→Function — **B3**
- [ ] Unique `code_node_id_unique`, repo/qname indexes, and vector `function_embedding_idx` — **B2**

### Required environment variables

- [ ] `GIT_AUTHOR_NAME` — **B1**
- [ ] `GIT_AUTHOR_EMAIL` — **B1**
- [ ] `DATABASE_URL` — **B1/B2**
- [ ] `NEO4J_URI` — **B1/B2**
- [ ] `NEO4J_USER` — **B1/B2**
- [ ] `NEO4J_PASSWORD` — **B1/B2**
- [ ] `REDIS_URL` — **B1/B2**
- [ ] `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` — **B1/B2/B6**
- [ ] `CLERK_SECRET_KEY` — **B2**
- [ ] `CLERK_JWKS_URL` — **B2**
- [ ] `OPENAI_API_KEY` — **B5**
- [ ] `ENCRYPTION_SECRET_KEY` — **B2**

## Batch status

- [ ] B1 — Phase 1
- [ ] B2 — Phase 2
- [ ] B3 — Phase 3
- [ ] B4 — Phase 4
- [ ] B5 — Phase 5
- [ ] B6 — Phase 6
- [ ] B7 — Prompt 14 QA
- [ ] B8 — Prompt 15 debugging
- [ ] B9 — Prompt 16 security
- [ ] B10 — Prompt 17 review/refactor
- [ ] B11 — Prompt 18 deployment
- [ ] B12 — Prompt 19 monitoring
- [ ] B13 — Prompt 20 final audit

The checklist is intentionally unchecked until the corresponding implementation
and real verification command has completed. Environment-only blockers will be
recorded beside the affected item rather than falsely marked as passed.
