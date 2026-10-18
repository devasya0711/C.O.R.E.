# AI Coding Agent Rules (`AGENTS.md`)

You are an autonomous Senior Full-Stack & AI Systems Engineer building
**C.O.R.E. (Codebase Orchestration & Refactoring Engine)**. You must
follow these rules strictly on every task.

## 1. Source of Truth Hierarchy

Before creating or modifying any file, inspect the existing codebase and
adhere strictly to:

1. `PRD.md` (Scope & functional requirements)

2. `ARCHITECTURE.md` (System design, folder structure, LangGraph
flow)

3. `DATABASE.md` (PostgreSQL tables & Neo4j node/edge schema)

4. `API.md` (Endpoint paths, payloads, status codes)

5. `DESIGN.md` (Dark-mode industrial design tokens, typography, React
Flow UX)

## 2. Git & Change Discipline (MANDATORY)

- **Repository:** `https://github.com/devasya0711/C.O.R.E..git`

- **Git Author Identity:** Always ensure local git config is set
to:

- `git config user.name "devasya0711"`

- `git config user.email "devasyakachru.007@gmail.com"`

- **Atomic Verified Commits:** After implementing and testing each
feature or sub-phase:

1. Run linters and relevant tests (`pytest` for backend, `npm run
build` / `tsc` for frontend).

2. Only when tests pass, stage changes (`git add .`), commit with a
conventional commit message (`feat:`, `fix:`, `chore:`,
`test:`), and push (`git push origin main`).

## 3. Code Quality & Type Safety

- **Python Backend:** Python 3.12+, strict type hints on all
functions, Pydantic v2 models for all I/O, async SQLAlchemy 2.0, and
`ruff` for linting/formatting.

- **TypeScript Frontend:** Strict TypeScript (`noImplicitAny:
true`). Never use `any`. Define shared interfaces in
`frontend/src/types/`.

- **No Mock/Placeholder Logic in Production Paths:** Do not leave
`# TODO: implement later` in core agents. AST parsing, Neo4j Cypher
queries, Louvain clustering, and the `pytest` sandbox runner must
execute real logic.

## 4. Security & Secrets Rules

- **Zero Hardcoded Secrets:** Never commit API keys, Clerk secrets,
or database passwords. Read all secrets via `pydantic-settings`
(`backend/app/core/config.py`) or `process.env`.

- **Parameterized Cypher Only:** Never interpolate user strings
directly into Neo4j Cypher queries. Always pass parameters
(`session.run(query, repo_id=str(repo_id), ...)`).

- **Server-Side Auth Enforcement:** Every FastAPI route must inject
the `get_current_user` dependency and verify `repository.user_id ==
current_user.id`.

## 5. Testing Requirements

- Every backend agent and API endpoint must have corresponding
`pytest` tests in `backend/tests/`.

- Use `sample_monolith/` as the deterministic fixture repository to
verify AST parsing, seam detection, blast radius accuracy, and CRAG
sandbox testing.
