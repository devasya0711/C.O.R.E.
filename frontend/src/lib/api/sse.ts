import type { MigrationEvent, MigrationEventType } from "@/types/migrations";

export interface ParsedSseBlock {
  id?: string;
  event?: string;
  data?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseSseBlock(block: string): ParsedSseBlock | null {
  if (!block.trim()) return null;
  const result: ParsedSseBlock = {};
  const dataLines: string[] = [];
  for (const line of block.replaceAll("\r\n", "\n").split("\n")) {
    if (line.startsWith("id:")) {
      result.id = line.slice(3).trim();
    } else if (line.startsWith("event:")) {
      result.event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length > 0) result.data = dataLines.join("\n");
  return result;
}

export function parseMigrationEvent(parsed: ParsedSseBlock): MigrationEvent | null {
  if (!parsed.data) return null;
  try {
    const payload: unknown = JSON.parse(parsed.data);
    if (!isRecord(payload)) return null;
    const jobId = payload.job_id;
    const repoId = payload.repo_id;
    const eventType = payload.event_type;
    if (typeof jobId !== "string" || typeof repoId !== "string" || typeof eventType !== "string") return null;
    const rawPayload = payload.payload;
    return {
      job_id: jobId,
      repo_id: repoId,
      event_type: eventType as MigrationEventType,
      payload: isRecord(rawPayload) ? rawPayload : {},
      timestamp: typeof payload.timestamp === "string" ? payload.timestamp : new Date().toISOString(),
      stream_id: parsed.id,
    };
  } catch {
    return null;
  }
}
