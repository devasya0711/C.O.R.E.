import { apiRequest } from "@/lib/api/client";
import type { GraphResponse } from "@/types/graph";
import type {
  RepositoryAccepted,
  RepositoryCreatePayload,
  RepositorySummary,
} from "@/types/repositories";

export function listRepositories(
  token: string,
  signal?: AbortSignal,
): Promise<RepositorySummary[]> {
  return apiRequest<RepositorySummary[]>("/api/v1/repos", token, { signal });
}

export function createRepository(
  token: string,
  payload: RepositoryCreatePayload,
): Promise<RepositoryAccepted> {
  return apiRequest<RepositoryAccepted>("/api/v1/repos", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRepositoryGraph(
  token: string,
  repositoryId: string,
  signal?: AbortSignal,
): Promise<GraphResponse> {
  return apiRequest<GraphResponse>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/graph`,
    token,
    { signal },
  );
}
