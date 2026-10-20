import { apiRequest } from "@/lib/api/client";
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
