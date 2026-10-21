import { apiRequest } from "@/lib/api/client";
import type { ImpactRequest, ImpactResponse, SeamsResponse } from "@/types/analysis";

export function analyzeSeams(token: string, repositoryId: string): Promise<SeamsResponse> {
  return apiRequest<SeamsResponse>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/seams/analyze`,
    token,
    { method: "POST" },
  );
}

export function analyzeImpact(
  token: string,
  repositoryId: string,
  payload: ImpactRequest,
): Promise<ImpactResponse> {
  return apiRequest<ImpactResponse>(
    `/api/v1/repos/${encodeURIComponent(repositoryId)}/impact`,
    token,
    { method: "POST", body: JSON.stringify(payload) },
  );
}
