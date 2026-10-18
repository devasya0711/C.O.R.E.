# Database Specification: C.O.R.E.

## 1. Database Technologies

1. **PostgreSQL 16:** System of record for users, repositories,
ingestion jobs, discovered seam clusters, migration jobs, and encrypted
GitHub tokens.

2. **Neo4j 5 (with APOC & GDS):** System of record for AST nodes,
call/data relationships, community IDs, and 1536-dimensional code
embeddings.

3. **Redis 7:** Ephemeral cache for Blast Radius queries (`TTL:
300s`), rate-limiting sliding windows, and SSE job event streams.

---

## 2. PostgreSQL Schema

### 2.1 `users`

| Column | Type | Constraints | Description |

|---|---|---|---|

| `id` | `UUID` | `PK, DEFAULT gen_random_uuid()` | Internal
user ID |

| `clerk_id` | `VARCHAR(128)` | `UNIQUE, NOT NULL, INDEX` |
Clerk external subject ID |

| `email` | `VARCHAR(255)` | `UNIQUE, NOT NULL` | User email
|

| `encrypted_github_pat` | `TEXT` | `NULLABLE` | Fernet
AES-encrypted GitHub token |

| `created_at` | `TIMESTAMPTZ` | `NOT NULL, DEFAULT now()` |
Creation timestamp |

### 2.2 `repositories`

| Column | Type | Constraints | Description |

|---|---|---|---|

| `id` | `UUID` | `PK, DEFAULT gen_random_uuid()` | Repository
ID (used as `repo_id` in Neo4j) |

| `user_id` | `UUID` | `FK -> users.id ON DELETE CASCADE,
INDEX` | Owner user ID |

| `name` | `VARCHAR(255)` | `NOT NULL` | Repo display name
(e.g., `org/monolith`) |

| `repo_url` | `VARCHAR(512)` | `NOT NULL` | GitHub URL or
`local://sample_monolith` |

| `default_branch` | `VARCHAR(128)` | `NOT NULL, DEFAULT
'main'` | Target branch |

| `last_commit_sha` | `VARCHAR(64)` | `NULLABLE` | Last
ingested commit hash |

| `status` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'PENDING'` |
`PENDING`, `INGESTING`, `READY`, `FAILED` |

| `node_count` | `INTEGER` | `NOT NULL, DEFAULT 0` | Total AST
nodes in Neo4j |

| `edge_count` | `INTEGER` | `NOT NULL, DEFAULT 0` | Total
relationships in Neo4j |

| `created_at` | `TIMESTAMPTZ` | `NOT NULL, DEFAULT now()` |
Creation timestamp |

### 2.3 `seam_clusters`

| Column | Type | Constraints | Description |

|---|---|---|---|

| `id` | `UUID` | `PK, DEFAULT gen_random_uuid()` | Seam
cluster ID |

| `repo_id` | `UUID` | `FK -> repositories.id ON DELETE
CASCADE, INDEX` | Parent repository |

| `community_id` | `INTEGER` | `NOT NULL` | Louvain cluster
integer ID |

| `suggested_name` | `VARCHAR(128)` | `NOT NULL` |
LLM-synthesized service name (e.g., `payment-service`) |

| `cohesion_score` | `FLOAT` | `NOT NULL` | Internal vs.
external edge ratio (`0.0 - 1.0`) |

| `shared_db_tables` | `JSONB` | `NOT NULL, DEFAULT '[]'` |
List of DB tables shared with other clusters |

| `node_ids` | `JSONB` | `NOT NULL, DEFAULT '[]'` | Array of
Neo4j `qualified_name` identifiers |

### 2.4 `migration_jobs`

| Column | Type | Constraints | Description |

|---|---|---|---|

| `id` | `UUID` | `PK, DEFAULT gen_random_uuid()` | Migration
job ID |

| `repo_id` | `UUID` | `FK -> repositories.id ON DELETE
CASCADE, INDEX` | Target repository |

| `seam_id` | `UUID` | `FK -> seam_clusters.id ON DELETE
CASCADE` | Target seam cluster |

| `status` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'QUEUED'` |
`QUEUED`, `CRAG_EVAL`, `CODING`, `TESTING`, `COMPLETED`,
`FAILED` |

| `crag_status` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'PENDING'`
| `SUFFICIENT`, `AUGMENTED` |

| `retry_count` | `INTEGER` | `NOT NULL, DEFAULT 0` | Number
of `pytest` sandbox retries (`0..3`) |

| `generated_artifacts` | `JSONB` | `NOT NULL, DEFAULT '{}'` |
Map of relative file paths to generated code |

| `sandbox_logs` | `TEXT` | `NULLABLE` | Captured `pytest`
stdout/stderr |

| `pr_url` | `VARCHAR(512)` | `NULLABLE` | Resulting GitHub
Pull Request URL |

| `created_at` | `TIMESTAMPTZ` | `NOT NULL, DEFAULT now()` |
Job start time |

---

## 3. Relational ER Diagram

```mermaid

erDiagram

USERS ||--o{ REPOSITORIES : owns

REPOSITORIES ||--o{ SEAM_CLUSTERS : contains

REPOSITORIES ||--o{ MIGRATION_JOBS : runs

SEAM_CLUSTERS ||--o{ MIGRATION_JOBS : targets

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

-   embedding: LIST<FLOAT> (1536-d vector index on :Function)

### **4.2 Relationship Types**

-   (:File)-[:CONTAINS]->(:Class | :Function)

-   (:Class)-[:CONTAINS]->(:Function)

-   (:Function)-[:CALLS {line: INTEGER}]->(:Function)

-   (:Class)-[:INHERITS]->(:Class)

-   (:Function)-[:READS_FROM]->(:DBTable)

-   (:Function)-[:WRITES_TO]->(:DBTable)

-   (:APIEndpoint)-[:ROUTES_TO]->(:Function)

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

OPTIONS {indexConfig: {`vector.dimensions`: 1536,
`vector.similarity_function`: 'cosine'}};

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
