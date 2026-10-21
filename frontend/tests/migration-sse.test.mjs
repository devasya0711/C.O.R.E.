import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parseMigrationEvent, parseSseBlock } = require("../node_modules/.cache/core-auth-tests/lib/api/sse.js");

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
});
