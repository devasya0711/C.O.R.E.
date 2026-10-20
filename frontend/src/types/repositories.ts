export type RepositoryStatus = "PENDING" | "INGESTING" | "READY" | "FAILED";

export type IngestionStage =
  | "QUEUED"
  | "CLONING"
  | "AST_PARSING"
  | "VECTOR_EMBEDDING"
  | "GRAPH_INDEXING";

export interface IngestionProgress {
  id: string;
  status: string;
  stage: string;
  progress: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepositorySummary {
  id: string;
  name: string;
  repo_url: string;
  default_branch: string;
  last_commit_sha: string | null;
  status: string;
  node_count: number;
  edge_count: number;
  created_at: string;
  ingestion: IngestionProgress | null;
}

export interface RepositoryCreatePayload {
  name: string;
  repo_url: string;
  default_branch: string;
  use_sample_monolith: boolean;
}

export interface RepositoryAccepted {
  repo_id: string;
  status: "INGESTING";
}
