import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  parseMigrationEvent,
  parseSseBlock,
  streamSseWithReconnect,
} = require("../node_modules/.cache/core-auth-tests/lib/api/sse.js");

test("SSE parser preserves event IDs and joins multiline data", () => {
  const parsed = parseSseBlock("id: 12-0\nevent: COMPLETED\ndata: {\"job_id\":\"job\",\ndata: \"ignored\"}\n");
  assert.deepEqual(parsed, {
    id: "12-0",
    event: "COMPLETED",
    data: '{"job_id":"job",\n"ignored"}',
  });
});

test("migration event parser rejects malformed payloads and keeps safe fields", () => {
  assert.equal(parseMigrationEvent({ data: "not-json" }), null);
  assert.equal(parseMigrationEvent({ data: JSON.stringify({ job_id: "job" }) }), null);

  const event = parseMigrationEvent({
    id: "3-0",
    event: "SANDBOX_ITERATION",
    data: JSON.stringify({
      job_id: "job",
      repo_id: "repo",
      event_type: "SANDBOX_ITERATION",
      payload: { passed: true, retry: 1 },
      timestamp: "2026-10-21T00:00:00Z",
    }),
  });
  assert.deepEqual(event, {
    job_id: "job",
    repo_id: "repo",
    event_type: "SANDBOX_ITERATION",
    payload: { passed: true, retry: 1 },
    timestamp: "2026-10-21T00:00:00Z",
    stream_id: "3-0",
  });
  assert.equal(
    parseMigrationEvent({
      data: JSON.stringify({
        job_id: "job",
        repo_id: "repo",
        event_type: "UNKNOWN_EVENT",
        payload: {},
      }),
    }),
    null,
  );
});

function eventFrame(id, eventType) {
  return [
    `id: ${id}`,
    `event: ${eventType}`,
    `data: ${JSON.stringify({
      job_id: "job",
      repo_id: "repo",
      event_type: eventType,
      payload: {},
      timestamp: "2026-10-21T00:00:00Z",
    })}`,
    "",
    "",
  ].join("\n");
}

function responseWithStream(chunks, failure) {
  let index = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        const chunk = chunks[index];
        controller.enqueue(typeof chunk === "string" ? new TextEncoder().encode(chunk) : chunk);
        index += 1;
        return;
      }
      if (failure) {
        controller.error(new Error(failure));
      } else {
        controller.close();
      }
    },
  }));
}

test("SSE reconnects after a read failure and preserves the last event ID", async () => {
  const requestedIds = [];
  const received = [];
  let attempt = 0;

  await streamSseWithReconnect(
    async (lastEventId) => {
      requestedIds.push(lastEventId);
      attempt += 1;
      if (attempt === 1) {
        return responseWithStream([eventFrame("1-0", "AGENT_START")], "stream reset");
      }
      return responseWithStream([eventFrame("2-0", "COMPLETED")], null);
    },
    (event) => received.push(event),
    new AbortController().signal,
    { maxReconnects: 2, reconnectDelayMs: 0 },
  );

  assert.deepEqual(requestedIds, [undefined, "1-0"]);
  assert.deepEqual(received.map((event) => event.stream_id), ["1-0", "2-0"]);
});

test("SSE reconnects after request and decode failures", async () => {
  const requestedIds = [];
  const received = [];
  let attempt = 0;

  await streamSseWithReconnect(
    async (lastEventId) => {
      requestedIds.push(lastEventId);
      attempt += 1;
      if (attempt === 1) throw new Error("network unavailable");
      if (attempt === 2) return responseWithStream([new Uint8Array([0xff])], null);
      return responseWithStream([eventFrame("3-0", "COMPLETED")], null);
    },
    (event) => received.push(event),
    new AbortController().signal,
    { maxReconnects: 3, reconnectDelayMs: 0 },
  );

  assert.deepEqual(requestedIds, [undefined, undefined, undefined]);
  assert.deepEqual(received.map((event) => event.stream_id), ["3-0"]);
});
