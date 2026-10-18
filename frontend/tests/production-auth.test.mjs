import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { after, before, test } from "node:test";
import { setTimeout } from "node:timers/promises";

const require = createRequire(import.meta.url);
let server;
let origin;
let output = "";

before(async () => {
  const listener = createServer();
  listener.listen(0, "127.0.0.1");
  await once(listener, "listening");
  const port = listener.address().port;
  await new Promise((resolve, reject) => listener.close((error) => error ? reject(error) : resolve()));
  origin = `http://127.0.0.1:${port}`;

  const environment = { ...process.env, NEXT_TELEMETRY_DISABLED: "1" };
  for (const name of Object.keys(environment)) {
    if (name.startsWith("CLERK_") || name.startsWith("NEXT_PUBLIC_CLERK_")) {
      delete environment[name];
    }
  }
  // Empty overrides also prevent Next.js from loading credentials from .env.local.
  environment.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = "";
  environment.CLERK_SECRET_KEY = "";

  server = spawn(process.execPath, [
    require.resolve("next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", String(port),
  ], { cwd: new URL("..", import.meta.url), env: environment, stdio: ["ignore", "pipe", "pipe"] });
  server.stdout.on("data", (chunk) => { output += chunk.toString(); });
  server.stderr.on("data", (chunk) => { output += chunk.toString(); });
  await once(server, "spawn");

  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (server.exitCode !== null) assert.fail(`Production server exited:\n${output}`);
    try {
      const response = await fetch(origin, { signal: AbortSignal.timeout(2_000) });
      if (response.status === 200) return;
    } catch {
      // Wait for the production listener, without masking a failed startup.
    }
    await setTimeout(250);
  }
  assert.fail(`Production server did not become ready:\n${output}`);
}, { timeout: 60_000 });

after(async () => {
  if (server && server.exitCode === null) {
    const exited = once(server, "exit");
    server.kill("SIGTERM");
    await exited;
  }
});

test("keyless production landing presents a static architecture preview", async () => {
  const response = await fetch(origin);
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.match(html, /ARCHITECTURE PREVIEW/);
  assert.match(html, /does not report service/);
  assert.doesNotMatch(html, /SYSTEM SIGNALS|>READY<|>STANDBY<|>ISOLATED</);
});

test("keyless production sign-in/up and nested steps show a configuration notice", async () => {
  for (const path of ["/sign-in", "/sign-in/factor-one", "/sign-up", "/sign-up/verify-email-address"]) {
    const response = await fetch(`${origin}${path}`);
    assert.equal(response.status, 200, path);
    assert.match(await response.text(), /Authentication needs configuration/, path);
  }
});

test("keyless production protects workspace routes, static suffixes, RSC, and POST", async () => {
  for (const path of [
    "/dashboard", "/dashboard/activity", "/repos", "/repos/example/graph",
    "/repos/example/migrations/job", "/repos/export.js", "/repos/diagram.svg",
    "/settings", "/settings/github", "/settings/account.css", "/settings?download=report.js",
  ]) {
    const response = await fetch(`${origin}${path}`, {
      redirect: "manual",
      headers: { "x-clerk-auth-status": "signed-in", cookie: "__session=not-a-session" },
    });
    assert.equal(response.status, 503, path);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal((await response.json()).error.code, "AUTH_NOT_CONFIGURED");
  }

  for (const options of [{ method: "POST" }, { headers: { RSC: "1" } }]) {
    const response = await fetch(`${origin}/settings`, options);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, "AUTH_NOT_CONFIGURED");
  }
});
