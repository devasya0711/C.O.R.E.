# API & Integration Specification: C.O.R.E.

## 1. Base Conventions & Authentication

- **Base URL:** `/api/v1`

- **Authentication:** Every endpoint (except `/api/v1/health`)
requires `Authorization: Bearer <CLERK_SESSION_JWT>`. FastAPI
verifies the token against Clerk's JWKS endpoint and resolves the
internal `user_id`.

- **Standard Error Format:**

```json

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
    > connectivity. Returns 200 only when all required stores are healthy.
    > The response includes `components` entries with `status`, `detail`, and
    > `latency_ms`, plus `postgres_ms`, `neo4j_ms`, and `redis_ms`. If any
    > required store is missing or unreachable, it returns `503
    > SERVICE_UNAVAILABLE` with the same component diagnostics in the standard
    > error `details` object.

-   **PUT /api/v1/settings/github-token** — Encrypts and saves a user's
    > GitHub PAT. Request body: `{ "token": "ghp_..." }`. Response:
    > `{ "status": "saved" }`; the token is never returned.

### **2.2 Repositories & AST Ingestion (Agent 1)**

-   **POST /api/v1/repos**

    -   **Purpose:** Register and trigger AST + GraphRAG ingestion for a
        > repository (or use_sample_monolith: true).

    -   **Request Body:**

    -   JSON

{

"name": "acme/legacy-shop",

"repo_url":
"[https://github.com/acme/legacy-shop.git](https://github.com/acme/legacy-shop.git)",

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

"seams": [

{

"id": "uuid",

"community_id": 2,

"suggested_name": "payment-service",

"cohesion_score": 0.87,

"shared_db_tables": ["orders_table"],

"node_ids": ["payments.processor.PaymentProcessor.execute"]

}

]

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

"affected_nodes": ["orders.checkout.place_order",
"api.routes.checkout_endpoint"],

"affected_edges": [{"source": "orders.checkout.place_order", "target":
"payments.processor.PaymentProcessor.execute", "hops": 1}],

"affected_db_tables": ["ledger_entries"],

"impact_summary": "High risk: Breaking signature affects 1 public API
route and 2 upstream modules."

}

-   
-   

### **2.5 Migration Orchestrator, CRAG Sandbox & GitHub PR (Agent 4)**

-   **POST /api/v1/repos/{repo_id}/migrations**

    -   **Purpose:** Starts the LangGraph workflow (CRAG evaluation ->
        > Code generation -> Pytest sandbox verification -> Optional
        > GitHub PR).

    -   **Request Body:** { "seam_id": "uuid", "create_github_pr": true
        > }

    -   **Response (202 Accepted):** { "job_id": "uuid", "status":
        > "QUEUED" }

-   **GET /api/v1/repos/{repo_id}/migrations/{job_id}/stream**

    -   **Purpose:** Server-Sent Events (SSE) stream emitting real-time
        > state updates. Each frame contains `id`, `event`, and JSON `data`.
        > The JSON event payload contains `job_id`, `repo_id`, `event_type`,
        > `payload`, and `timestamp`. Clients may send `Last-Event-ID` to
        > replay events after reconnecting. Event types are
        > `AGENT_START`, `CRAG_DECISION`, `SANDBOX_ITERATION`,
        > `ARTIFACTS_READY`, `PR_CREATED`, `MIGRATION_FAILED`, `HUMAN_REVIEW`,
        > and `COMPLETED`.

-   **GET /api/v1/repos/{repo_id}/migrations/{job_id}**

    -   **Purpose:** Returns owner-scoped migration status, CRAG/sandbox
        > verification, generated artifacts, and an optional PR URL for the
        > review screen.

    -   **Response (200 OK):** Includes `job_id`, `repo_id`, `seam_id`,
        > `status` (`QUEUED`, `CRAG_EVAL`, `CODING`, `TESTING`, `COMPLETED`,
        > or `FAILED`), `create_github_pr`, `crag_status`, `retry_count`,
        > `generated_artifacts`, `sandbox_logs`, `sandbox_passed`,
        > `sandbox_isolation`, `sandbox_isolation_note`, `pr_url`, and
        > `created_at`.

-   **POST /api/v1/repos/{repo_id}/migrations/{job_id}/pr**

    -   **Purpose:** Manually triggers GitHub branch push and Pull
        > Request creation for a completed, sandbox-verified migration job.
        > Request body: `{ "job_id": "uuid", "title": "Extract service" }`.
        > The repository source must be a GitHub URL; local sample repositories
        > return `GITHUB_REPOSITORY_REQUIRED`.

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

# Git & Project Identity

GIT_AUTHOR_NAME=devasya0711

GIT_AUTHOR_EMAIL=devasyakachru.007@gmail.com

# Databases

DATABASE_URL=postgresql+asyncpg://core_user:core_pass@postgres:5432/core_db

NEO4J_URI=bolt://neo4j:7687

NEO4J_USER=neo4j

NEO4J_PASSWORD=core_neo4j_pass

REDIS_URL=redis://redis:6379/0

# Authentication (Clerk)

NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...

CLERK_SECRET_KEY=sk_test_...

CLERK_JWKS_URL=https://<your-clerk-domain>/.well-known/jwks.json

# AI & Security

OPENAI_API_KEY=sk-...

ENCRYPTION_SECRET_KEY=<32-byte-base64-fernet-key>

---
