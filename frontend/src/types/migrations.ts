export type MigrationEventType =
  | "AGENT_START"
  | "CRAG_DECISION"
  | "SANDBOX_ITERATION"
  | "ARTIFACTS_READY"
  | "PR_CREATED"
  | "MIGRATION_FAILED"
  | "HUMAN_REVIEW"
  | "COMPLETED";

export interface MigrationAccepted {
  job_id: string;
  status: "QUEUED";
}

export interface MigrationEvent {
  job_id: string;
  repo_id: string;
  event_type: MigrationEventType;
  payload: Record<string, unknown>;
  timestamp: string;
  stream_id?: string;
}

export interface MigrationPrResponse {
  job_id: string;
  status: "PR_CREATED" | "COMPLETED";
  pr_url: string | null;
  pr_number: number | null;
  branch: string | null;
}

export type MigrationJobStatus =
  | "QUEUED"
  | "CRAG_EVAL"
  | "CODING"
  | "TESTING"
  | "COMPLETED"
  | "FAILED";

export interface MigrationJobDetail {
  job_id: string;
  repo_id: string;
  seam_id: string;
  status: MigrationJobStatus;
  create_github_pr: boolean;
  crag_status: string;
  retry_count: number;
  generated_artifacts: Record<string, string>;
  sandbox_logs: string | null;
  sandbox_passed: boolean;
  sandbox_isolation: string | null;
  sandbox_isolation_note: string | null;
  pr_url: string | null;
  created_at: string;
}

export const terminalMigrationEvents = new Set<MigrationEventType>([
  "PR_CREATED",
  "MIGRATION_FAILED",
  "HUMAN_REVIEW",
  "COMPLETED",
]);
