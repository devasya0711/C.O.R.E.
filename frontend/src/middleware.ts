import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

import { getClerkConfiguration } from "./lib/auth/config";

const isProtectedRoute = createRouteMatcher([
  "/dashboard(.*)",
  "/repos(.*)",
  "/settings(.*)",
]);

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  const configuration = getClerkConfiguration();

  if (!configuration) {
    if (isProtectedRoute(request)) {
      return NextResponse.json(
        {
          error: {
            code: "AUTH_NOT_CONFIGURED",
            message: "Authentication is unavailable. Configure the Clerk publishable and secret keys.",
          },
        },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }

    return NextResponse.next();
  }

  return clerkMiddleware(async (auth, clerkRequest) => {
    if (isProtectedRoute(clerkRequest)) {
      await auth.protect({ token: "session_token" });
    }
  }, {
    publishableKey: configuration.publishableKey,
    secretKey: configuration.secretKey,
    signInUrl: configuration.signInUrl,
    signUpUrl: configuration.signUpUrl,
  })(request, event);
}

export const config = {
  matcher: [
    // Always inspect protected paths, including paths that look like static files.
    "/dashboard(.*)",
    "/repos(.*)",
    "/settings(.*)",
    // Run Clerk on public pages too, so its provider can resolve the real session.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
