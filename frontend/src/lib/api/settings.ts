import { apiRequest } from "@/lib/api/client";
import type { GithubTokenResponse } from "@/types/settings";

export function saveGithubToken(token: string, githubToken: string): Promise<GithubTokenResponse> {
  return apiRequest<GithubTokenResponse>("/api/v1/settings/github-token", token, {
    method: "PUT",
    body: JSON.stringify({ token: githubToken }),
  });
}
