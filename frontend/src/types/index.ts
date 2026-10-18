export type HealthState = "healthy" | "unhealthy" | "not_configured";

export interface ComponentHealth {
  status: HealthState;
  latency_ms: number | null;
  detail: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  components: Record<string, ComponentHealth>;
}
