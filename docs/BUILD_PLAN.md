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
   (`gpt-4o` and `text-embedding-3-small`, as named by the PRD). Phase 3
   supersedes this plan's original offline-ingestion assumption: absent
   `OPENAI_API_KEY` produces an explicit `EMBEDDING_NOT_CONFIGURED` job failure,
   never synthetic/hash vectors or silent provider substitution. Test-only HTTP
   fixtures exercise the provider interface. Production code has a real OpenAI
   client; a live key is still needed for live embedding verification.
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
16. **Phase 2 identity provisioning.** Clerk's default session JWT does not
    necessarily contain an email. Verified subjects are looked up by
    `users.clerk_id`; new subjects use a signed custom `email` claim when
    present, otherwise the primary email is fetched from Clerk's Backend API
    using runtime `CLERK_SECRET_KEY`. No synthetic emails or email-based account
    linking are used. JWT verification pins RS256, issuer, required timestamps,
    and optionally audience/authorized parties.
17. **GitHub token settings payload.** API.md specifies the PUT path but no
    JSON field or success body. Phase 2 uses `{ "token": "..." }` and returns
    HTTP 200 `{ "status": "saved" }`, never plaintext or ciphertext.
18. **Ownership denial.** Prompt 11 explicitly requires cross-tenant HTTP 403.
    The shared ownership dependency queries both repository ID and verified
    owner ID and returns the same 403 for foreign and nonexistent IDs to avoid
    leaking existence. It does not perform an unscoped existence query.
19. **Fernet versus AES-256 wording.** The explicit library/key requirement is
    followed: `cryptography.fernet.Fernet` with a base64-encoded 32-byte key.
    Fernet actually uses AES-128-CBC plus HMAC-SHA256, not AES-256. This
    specification conflict is recorded rather than falsely advertising
    AES-256 or replacing the named encryption primitive.
20. **Authenticated rate-limit ordering.** The Redis Lua sliding-window guard
    is installed as a protected-router dependency after `get_current_user`,
    rather than unauthenticated IP/header-based ASGI middleware. This enforces
    the specified 60/min and 10/min budgets using the verified internal user
    ID, and fails closed if Redis is unavailable.
21. **Configuration is not a live credential.** Empty external-credential
    values in `.env.example` remain unconfigured. Neither frontend nor API
    bypasses auth when Clerk is absent. Compose requires user-supplied store
    passwords/`DATABASE_URL`; Clerk secrets are runtime-only, never Docker
    build arguments. Test stores without auth are isolated local/CI fixtures,
    not production deployment defaults.
22. **Exact graph relationship schema.** Only the node-label pairings listed
    in DATABASE.md are emitted/validated. Module/class-initialization calls are
    diagnostics, not File/Class `CALLS` edges. Nested definitions retain their
    qualified names but use the containing File for `CONTAINS` where a Function
    or Class-to-Class container edge would violate that schema. Tables retain
    source metadata and read/write links, without invented `CONTAINS` edges.
23. **Ingestion durability and retry.** A Redis Streams consumer group is the
    worker queue; messages contain owner/repository/job UUIDs only. PostgreSQL
    rows are committed before enqueueing. A retry after a Redis enqueue failure
    republishes the existing QUEUED job; concurrent imports of the same
    owner/source/branch are serialized with a PostgreSQL advisory lock. Pending
    entries from a dead consumer are reclaimed after 300 seconds, beyond the
    bounded ingestion timeout. Repository leases prevent simultaneous writers.
24. **Progress contract.** No undocumented public SSE endpoint is added.
    `GET /api/v1/repos` includes the latest `ingestion` job's stage, progress,
    status, error, and timestamps for authorized polling. Redis retains a
    bounded one-day progress event stream/pub-sub per repository/job; migration
    SSE remains Phase 5's specified public streaming contract.
25. **Static parser resource boundary.** The API runs only the installed,
    trusted parser module in an isolated Python subprocess, with stripped
    environment, 768 MiB address-space/30-second CPU limits, a 35-second wall
    timeout, and bounded graph output. Target modules are never imported or
    executed. Tree-sitter is pinned to 0.25.2-compatible releases because
    0.26.0 crashed during source-location extraction in this environment.
26. **Source and fixture boundaries.** GitHub HTTPS only; no URL credentials,
    redirects, hooks, filters, submodules, symlinks, or arbitrary local paths.
    Public clones and PAT acquisition share the same inert blob-export path.
    The sample URL uses a deterministic source-content SHA (not a fabricated
    Git commit). The fixture now imports real SQLAlchemy Table/Column types and
    uses conventional `__tablename__` metadata on domain records instead of the
    initial scaffold's SQLAlchemy-like marker classes.

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

**Batch 2 implementation and verification:**

- [x] Exact `users`, `repositories`, `seam_clusters`, and `migration_jobs`
  PostgreSQL models plus `ingestion_jobs` (decision 6); async sessions, native
  UUID/JSONB/timestamptz defaults, cascade deletion, and value constraints.
- [x] Alembic revision `0001_postgresql`, async online upgrade/downgrade,
  offline SQL, and migration/model agreement. Docker startup runs
  `alembic upgrade head` before starting Uvicorn.
- [x] Neo4j async driver and idempotent startup uniqueness/repo/qname/vector
  index creation. Real Neo4j checks verify uniqueness enforcement and the
  1536-dimensional cosine vector index.
- [x] Async Clerk JWKS verification, safe provisioning, common ownership guard,
  401/403 tests, and encrypted token settings endpoint.
- [x] Fernet encryption/decryption/tamper tests; request schemas reject invalid
  tokens and error responses omit sensitive input values.
- [x] Real Redis atomic sliding-window tests: exactly ten of 25 concurrent
  job requests admitted, 60-request graph limit, user isolation, window expiry,
  retry headers, cache TTL, and pub/sub. Store outages fail closed.
- [x] Strict CORS configuration, standard API error envelopes, optional-secret
  normalization, and request-scoped transaction cleanup.
- [x] Clerk frontend middleware, styled catch-all sign-in/sign-up pages,
  dynamic provider/account controls, and production fail-closed route tests.
  The landing page now labels its diagram as an architecture preview, not a
  live operational status report.
- [x] Backend package now requires Python 3.12+ as specified.
- [x] Python 3.12.15: **145 passed, 2 intentional skips**, with live PostgreSQL
  16.15, Neo4j 5.26.0, and Redis 7.0.15. The skips are the SQLite variants of
  PostgreSQL-only native-type/VARCHAR checks; their PostgreSQL variants pass.
- [x] Ruff lint/format checks pass for all backend/sample files. Strict mypy
  (`--ignore-missing-imports`) passes for all 20 backend application modules.
- [x] Fresh frontend snapshot: `npm ci`, `npm run typecheck`, `npm run lint`,
  `npm test` (**7 passed**), `npm run build`, and `npm run test:smoke`
  (**3 passed**). The lockfile remains unchanged.
  An earlier direct workspace build exceeded the 300-second tool timeout;
  the fresh snapshot completed the same build and all checks without disabling
  lint/type validation. The timed-out invocation is not counted as a pass.
- [x] Real app lifespan/HTTP smoke: Neo4j schema initialization succeeds,
  `/api/v1/health` returns 200 with all three native stores healthy, and
  unauthenticated token settings return 401. Missing stores now correctly
  return 503 instead of the initial scaffold's misleading aggregate `ok`.
- [x] Docker Compose v2.39.4 `config --format json` validates all five services
  with ephemeral credentials, without printing them. CI now provisions all
  three real test stores and runs frontend auth tests/build/smoke checks.
- [ ] Container image builds/cold boot still require a Docker daemon, which is
  unavailable in this runner. Native-service verification above is not claimed
  as container boot verification.
- [ ] Live Clerk sign-in/session refresh/sign-out requires a matching Clerk
  application and keys. Local tests verify real RSA JWT logic with generated
  keys and a test-only HTTP provider boundary; they are not live Clerk tests.
- [x] Phase 2 commit `7e54424` is pushed: local `origin/main` was observed at
  that commit before starting Phase 3.
- [ ] GitHub Actions results still need authenticated remote verification.
  The runner's SSH identity is separate from the user's WSL session.

**Reproducing Phase 2 checks:** install `backend[dev]` in Python 3.12+, then
run `ruff check backend sample_monolith`,
`ruff format --check backend sample_monolith`, and
`PYTHONPATH=. python -m pytest sample_monolith backend/tests` from the root.
Set `TEST_DATABASE_URL` to a disposable PostgreSQL 16 database with schema
creation privileges, `TEST_REDIS_URL` to a disposable Redis 7 database, and
`TEST_NEO4J_URI` (plus `TEST_NEO4J_USER`/`TEST_NEO4J_PASSWORD` if needed) to a
Neo4j 5 instance to enable all real-store cases. Without these variables,
integration cases are explicitly skipped, never substituted with fake stores.
Tests isolate/drop their own generated schemas/nodes/keys, not existing data.

Remaining warnings: upstream Starlette/httpx deprecation; Clerk 7's
`createRouteMatcher` deprecation (Phase 6 data-bearing pages must additionally
protect resources server-side); and the 11 previously reported npm advisories.
These are recorded gaps, not a clean final security/production verdict.

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

**Batch 3 implementation and verification:**

- [x] Tree-sitter Python analyzer with deterministic source extents, docstrings,
  import/receiver resolution, inheritance, route decorators, and SQLAlchemy/
  literal SQL data dependencies. Ambiguous dynamic bindings produce bounded
  diagnostics, not guessed call edges. Inputs are limited to 500 Python files
  and 1 MiB per Python file; tests/dependencies/symlinks are excluded.
- [x] Exact sample regression: **73 nodes** (6 File, 19 Class, 41 Function,
  1 APIEndpoint, 6 DBTable) and **132 relationships** (60 CONTAINS, 39 CALLS,
  21 READS_FROM, 11 WRITES_TO, 1 ROUTES_TO). Sample INHERITS count is zero;
  synthetic inheritance fixtures verify that relationship. The chained
  `orders.place_order -> payments.PaymentProcessor.execute` call is verified
  at line 98. Unresolved/external definitions remain 83 explicit diagnostics.
- [x] Secure source acquisition, temporary snapshot cleanup, immutable hashes,
  Git credential handling, file/disk limits, subprocess timeout/cancellation,
  and sample loading. A real public clone of `octocat/Hello-World` branch
  `master` succeeded and its temporary directory was removed afterward.
- [x] Actual OpenAI embedding HTTP client: configured model and 1536 dimensions,
  tokenizer-bounded inputs/batches, validated response count/order/vectors,
  30-second request deadlines and up to three backoff retries for transient
  timeout/429/5xx failures. No production embedding fallback exists.
- [x] Atomic tenant-scoped Neo4j batch upserts, stable IDs, stale-node/edge
  cleanup, schema validation, and React Flow-compatible JSON with inspector
  metadata and edge counts. Function vectors stay out of graph-view responses.
- [x] Hybrid retrieval combines tenant-filtered cosine ranking with one-hop
  structural neighbors. Neo4j 5 cannot prefilter its global vector top-k by
  tenant; repository-scoped scoring avoids cross-tenant candidates and the
  recall loss of filtering a global top-k after retrieval.
- [x] Authenticated POST/list/graph repository APIs. Reposting the same source
  reuses its repository namespace; completed/failed jobs get a new job, while
  concurrent posts share a pending job. Graph access is denied until READY.
- [x] Durable worker, dead-consumer recovery, atomic queue acknowledgement/
  deletion, repository leases, persisted stage transitions, and failure codes.
  Queue messages never contain PATs/source text. A competing lease never
  acknowledges an unprocessed job. Missing provider configuration and AST
  errors remain visible failures and do not fabricate a graph.
- [x] Backend Docker setup includes Git/CA certificates and pre-caches the
  tokenizer; Compose mounts the trusted sample read-only and forwards the
  required ingestion settings. Store ports are bound to host loopback.
- [x] Python 3.12.15: **284 passed, 2 intentional SQLite-only skips** in the
  final 39.02-second run using real PostgreSQL 16.15, Neo4j 5.26.0, and Redis
  7.0.15. This includes exact AST
  regressions, all repository APIs, tenant isolation, concurrent requests,
  queue outage/recovery, real Neo4j rollback/idempotency, and semantic isolation.
- [x] Ruff lint/format passed (53 Python files); strict mypy passed (31 backend
  application modules). Frontend typecheck, lint, 7 auth unit tests, production
  build, and 3 production auth smoke tests passed. The first build attempt hit
  the tool's four-minute timeout without a compiler error; rerunning the same
  `npm run build` with a larger timeout succeeded without source/dependency
  changes. The dependency lockfile is unchanged. No frontend UI source changed.
- [x] Provider-fixture benchmark with actual SQL/Redis/Neo4j: 500 Python files
  produced 1000 nodes/500 edges. Its test call took 2.24 seconds in the final
  combined run; sample ingestion plus re-ingestion passed the under-30-second
  assertion. These measurements exclude real OpenAI
  network latency and are not a verified production performance SLA.
- [x] Standalone Compose v2.39.4 `config --format json` validated all 5 services,
  the read-only sample mount, and loopback-only store ports with ephemeral
  credentials. No resolved credentials were printed or written into the repo.
- [ ] A live OpenAI embedding call/private GitHub PAT clone requires credentials
  not supplied to this runner. Provider HTTP behavior/credential containment
  are tested at explicit test boundaries, not represented as live service tests.
- [ ] ZIP upload remains the documented PRD/API contract gap (decision 2), not
  a silently accepted or fake source type. Dynamic dispatch, wildcard imports,
  complex SQL CTEs, and external definitions are explicit static-analysis
  limitations with diagnostics/full-source context for CRAG.
- [ ] Container build/cold boot requires a Docker daemon. Compose configuration
  can be validated here, but native-store tests are not container boot proof.
- [ ] Confirm this batch is pushed and verify its GitHub checks before Phase 4.

**Reproducing Phase 3 checks:** use the Python 3.12+ setup and disposable
`TEST_DATABASE_URL`, `TEST_REDIS_URL`, and `TEST_NEO4J_URI` configuration described
in Batch 2. Run `ruff check backend sample_monolith`,
`ruff format --check backend sample_monolith`,
`mypy --strict --ignore-missing-imports backend/app`, and
`PYTHONPATH=. python -m pytest sample_monolith backend/tests --durations=5 -q`
from the root. In `frontend/`, run `npm run typecheck`, `npm run lint`,
`npm test`, `npm run build`, and `npm run test:smoke`. The embedding provider
fixture is explicitly test-only; these commands require no live OpenAI key.

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

- [ ] **US-1.1** Submit GitHub URL/branch and build an interactive Neo4j graph — **B3/B6**; B3 backend verified, interactive studio remains B6
- [ ] **US-1.2** Stream/show cloning, AST, embedding, and graph-index progress — **B3/B5/B6**; B3 durable progress/polling verified, frontend/SSE work remains
- [ ] **US-2.1** Run Louvain and inspect cohesive seam clusters — **B4/B6**
- [ ] **US-2.2** Highlight cross-community `WRITES_TO`/`READS_FROM` shared tables — **B4/B6**
- [ ] **US-3.1** Simulate a signature change and trace one-to-five-hop callers/routes — **B4/B6**
- [ ] **US-3.2** Return an explained 0–100 Blast Radius Risk Score — **B4/B6**
- [ ] **US-4.1** Extract a seam into FastAPI service, Strangler Fig config, and adapter — **B5**
- [ ] **US-4.2** CRAG/pytest verify with up to three corrections before GitHub PR — **B5**

### Functional requirements

- [x] **FR-1 Static Analyzer:** Tree-sitter File/Class/Function/APIEndpoint/DBTable nodes, CALLS/INHERITS/READS_FROM/WRITES_TO edges, real embedding client — **B3**; live provider credential test remains
- [ ] **FR-2 Boundary Mapper:** GDS Louvain with NetworkX fallback, cohesion/coupling, shared-table conflicts — **B4**
- [ ] **FR-3 Impact Predictor:** bounded 1..5-hop Cypher, affected nodes/edges/routes, 0–100 risk — **B4**
- [ ] **FR-4 CRAG Evaluator:** completeness checks, unresolved import/type detection, full-file AST fallback — **B5**
- [ ] **FR-5 Refactor Coder & Sandbox:** service/Dockerfile/proxy/adapter artifacts, isolated pytest, stderr feedback, three retries — **B5**
- [ ] **FR-6 GitHub PR Engine:** `refactor/extract-{seam}`, verified commit, Mermaid graph, blast-radius table, PR — **B5**

### Non-functional requirements

- [ ] **Performance:** ≤500 ms five-hop graph query at 10,000 nodes and ≤90 s ingestion for 500 files — **B4/B7/B13**
- [ ] **Security:** Clerk JWT on API routes, AES-256/Fernet token encryption, parameterized Cypher, no-network isolated sandbox — **B2/B5/B9**
- [x] **Reliability:** idempotent re-ingestion updates existing nodes without duplicates — **B3** real-store verification; re-audit in **B7/B13**

### API contract

- [x] **GET `/api/v1/health`** real connectivity and failure checks — **B1/B2**; exact monitoring fields remain **B12**
- [x] **PUT `/api/v1/settings/github-token`** encrypted PAT storage — **B2** (decision 19 notes the Fernet/AES wording conflict)
- [x] **POST `/api/v1/repos`** register/trigger URL or sample ingestion, 202 — **B3**
- [x] **GET `/api/v1/repos`** list authenticated owner's repositories — **B3**
- [x] **GET `/api/v1/repos/{repo_id}/graph`** React Flow nodes/edges with community/shared-table fields — **B3**; computed community/shared-table values remain **B4**
- [ ] **POST `/api/v1/repos/{repo_id}/seams/analyze`** deterministic seam analysis — **B4**
- [ ] **POST `/api/v1/repos/{repo_id}/impact`** bounded impact request and risk response — **B4**
- [ ] **POST `/api/v1/repos/{repo_id}/migrations`** queue LangGraph migration, 202 — **B5**
- [ ] **GET `/api/v1/repos/{repo_id}/migrations/{job_id}/stream`** SSE state events — **B5**
- [ ] **POST `/api/v1/repos/{repo_id}/migrations/{job_id}/pr`** manual PR creation — **B5**
- [x] Standard `{error: {code, message, details}}` failures and ownership-safe 403 behavior — **B2**; re-audit in **B7/B9**
- [x] Redis sliding windows: 60/min graph/read and 10/min ingestion/migration per user — **B2**; re-audit in **B9**
- [ ] External resilience: LLM backoff max three, structured output, GitHub 15-second timeout — **B5/B9**

### Neo4j graph schema

- [x] Base `CodeNode` label with tenant `repo_id` and required source/location properties — **B3**
- [x] `File` node label — **B3**
- [x] `Class` node label — **B3**
- [x] `Function` node label with 1536-dimensional embedding index — **B2/B3**
- [x] `APIEndpoint` node label — **B3**
- [x] `DBTable` node label — **B3**
- [x] `CONTAINS` File→Class/Function and Class→Function — **B3**
- [x] `CALLS {line}` Function→Function — **B3**
- [x] `INHERITS` Class→Class — **B3**
- [x] `READS_FROM` Function→DBTable — **B3**
- [x] `WRITES_TO` Function→DBTable — **B3**
- [x] `ROUTES_TO` APIEndpoint→Function — **B3**
- [x] Unique `code_node_id_unique`, repo/qname indexes, and vector `function_embedding_idx` — **B2**

### Required environment variables

- [x] `GIT_AUTHOR_NAME` — **B1**
- [x] `GIT_AUTHOR_EMAIL` — **B1**
- [x] `DATABASE_URL` — **B1/B2**
- [x] `NEO4J_URI` — **B1/B2**
- [x] `NEO4J_USER` — **B1/B2**
- [x] `NEO4J_PASSWORD` — **B1/B2**
- [x] `REDIS_URL` — **B1/B2**
- [x] `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` — **B1/B2**; live credentials still needed
- [x] `CLERK_SECRET_KEY` — **B2**; runtime-only
- [x] `CLERK_JWKS_URL` — **B2**
- [x] `OPENAI_API_KEY` — **B3/B5**; typed runtime secret, missing-key failure tested; live key not supplied
- [x] `ENCRYPTION_SECRET_KEY` — **B2**; no key generated into tracked files

## Batch status

- [ ] B1 — Phase 1 implementation pushed; Docker cold boot still unverified
- [ ] B2 — Phase 2 implementation/tests pushed; live Clerk/remote CI unverified
- [ ] B3 — Phase 3 implementation/local verification done; push confirmation pending, live provider/Docker gaps above
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
