export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = {}) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function apiBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
    /\/$/u,
    "",
  );
}

function errorEnvelope(value: unknown): {
  code: string;
  message: string;
  details: unknown;
} | null {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return null;
  }

  const error = value.error;
  if (typeof error !== "object" || error === null) {
    return null;
  }

  const record = error as Record<string, unknown>;
  return {
    code: typeof record.code === "string" ? record.code : "API_ERROR",
    message:
      typeof record.message === "string" ? record.message : "The request was rejected.",
    details: record.details ?? {},
  };
}

async function responseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function apiRequest<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  const body = await responseBody(response);

  if (!response.ok) {
    const envelope = errorEnvelope(body);
    throw new ApiClientError(
      response.status,
      envelope?.code ?? "API_ERROR",
      envelope?.message ?? `Request failed with status ${response.status}.`,
      envelope?.details,
    );
  }

  return body as T;
}
