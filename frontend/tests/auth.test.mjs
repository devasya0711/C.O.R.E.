import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { NextRequest } = require("next/server");
const { unstable_doesMiddlewareMatch } = require("next/experimental/testing/server");
const { getClerkConfiguration } = require("../node_modules/.cache/core-auth-tests/lib/auth/config.js");
const { default: middleware, config } = require("../node_modules/.cache/core-auth-tests/middleware.js");

// Deliberately invalid sentinels, never API keys or sessions. Clerk must reject them.
const environmentSentinels = {
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "unit sentinel: publishable",
  CLERK_SECRET_KEY: "unit sentinel: secret",
};

const protectedPaths = [
  "/dashboard",
  "/dashboard/",
  "/dashboard/activity",
  "/dashboard-preview",
  "/repos",
  "/repos/repository-id/graph",
  "/repos/repository-id/migrations/job-id",
  "/repos/export.json",
  "/repos/export.js",
  "/repos/diagram.svg",
  "/settings",
  "/settings/github",
  "/settings/account.css",
];

async function withEnvironment(overrides, run) {
  const previous = { ...process.env };

  for (const name of Object.keys(process.env)) {
    if (name.startsWith("CLERK_") || name.startsWith("NEXT_PUBLIC_CLERK_")) {
      delete process.env[name];
    }
  }

  Object.assign(process.env, overrides);

  try {
    await run();
  } finally {
    for (const name of Object.keys(process.env)) {
      if (!(name in previous)) delete process.env[name];
    }
    Object.assign(process.env, previous);
  }
}

test("auth is unconfigured if either key is absent, empty, or whitespace", () => {
  for (const environment of [
    {},
    { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: environmentSentinels.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY },
    { CLERK_SECRET_KEY: environmentSentinels.CLERK_SECRET_KEY },
    { ...environmentSentinels, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "" },
    { ...environmentSentinels, CLERK_SECRET_KEY: " \n " },
  ]) {
    assert.equal(getClerkConfiguration(environment), null);
  }
});

test("configuration keeps sign-in/up and sign-out on existing public routes by default", () => {
  assert.deepEqual(getClerkConfiguration(environmentSentinels), {
    publishableKey: environmentSentinels.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
    secretKey: environmentSentinels.CLERK_SECRET_KEY,
    signInUrl: "/sign-in",
    signUpUrl: "/sign-up",
    signInFallbackRedirectUrl: "/",
    signUpFallbackRedirectUrl: "/",
    afterSignOutUrl: "/",
  });
});

test("configuration reads the exact public URL variables and trims whitespace", () => {
  assert.deepEqual(getClerkConfiguration({
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: ` ${environmentSentinels.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY} `,
    CLERK_SECRET_KEY: ` ${environmentSentinels.CLERK_SECRET_KEY} `,
    NEXT_PUBLIC_CLERK_SIGN_IN_URL: " /sign-in ",
    NEXT_PUBLIC_CLERK_SIGN_UP_URL: " /sign-up ",
    NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL: " /dashboard ",
    NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL: " /dashboard ",
    NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL: " /?signed-out=1 ",
  }), {
    publishableKey: environmentSentinels.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
    secretKey: environmentSentinels.CLERK_SECRET_KEY,
    signInUrl: "/sign-in",
    signUpUrl: "/sign-up",
    signInFallbackRedirectUrl: "/dashboard",
    signUpFallbackRedirectUrl: "/dashboard",
    afterSignOutUrl: "/?signed-out=1",
  });
});

test("Next.js invokes middleware for protected paths, even static-looking paths and queries", () => {
  for (const path of [...protectedPaths, "/settings?download=report.js", "/sign-in", "/sign-up/verify-email-address"]) {
    assert.equal(unstable_doesMiddlewareMatch({ config, url: `http://localhost${path}` }), true, path);
  }

  for (const path of ["/_next/static/chunks/app.js", "/favicon.ico", "/logo.svg"]) {
    assert.equal(unstable_doesMiddlewareMatch({ config, url: `http://localhost${path}` }), false, path);
  }
});

test("missing and partial configuration fail closed for every protected route", async () => {
  for (const environment of [
    {},
    { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: environmentSentinels.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY },
    { CLERK_SECRET_KEY: environmentSentinels.CLERK_SECRET_KEY },
  ]) {
    await withEnvironment(environment, async () => {
      for (const path of protectedPaths) {
        const response = await middleware(new NextRequest(`http://localhost${path}`, {
          headers: { "x-clerk-auth-status": "signed-in", cookie: "__session=not-a-session" },
        }), {});
        assert.equal(response.status, 503, path);
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("x-middleware-next"), null);
        assert.equal((await response.json()).error.code, "AUTH_NOT_CONFIGURED");
      }
    });
  }
});

test("missing configuration still allows landing and Clerk catch-all pages to render", async () => {
  await withEnvironment({}, async () => {
    for (const path of ["/", "/sign-in", "/sign-in/factor-one", "/sign-up", "/sign-up/verify-email-address"]) {
      const response = await middleware(new NextRequest(`http://localhost${path}`), {});
      assert.equal(response.headers.get("x-middleware-next"), "1", path);
    }
  });
});

test("nonempty malformed credentials reach Clerk validation and never bypass authentication", async () => {
  await withEnvironment(environmentSentinels, async () => {
    await assert.rejects(
      async () => middleware(new NextRequest("http://localhost/dashboard"), {}),
      /publishable key.*(?:invalid|not valid)/i,
    );
  });
});
