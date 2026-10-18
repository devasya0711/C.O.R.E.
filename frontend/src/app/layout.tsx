import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";

import { clerkAppearance } from "@/lib/auth/appearance";
import { getClerkConfiguration } from "@/lib/auth/config";
import type { LayoutChildrenProps } from "@/types/auth";

import "./globals.css";

export const metadata: Metadata = {
  title: "C.O.R.E. — Codebase Orchestration & Refactoring Engine",
  description: "Graph-aware orchestration for safe monolith refactoring.",
};

// Auth depends on the incoming session, never on credentials available during a build.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<LayoutChildrenProps>) {
  const configuration = getClerkConfiguration();

  return (
    <html lang="en" className="dark">
      <body>
        {configuration ? (
          <ClerkProvider
            dynamic
            publishableKey={configuration.publishableKey}
            signInUrl={configuration.signInUrl}
            signUpUrl={configuration.signUpUrl}
            signInFallbackRedirectUrl={configuration.signInFallbackRedirectUrl}
            signUpFallbackRedirectUrl={configuration.signUpFallbackRedirectUrl}
            afterSignOutUrl={configuration.afterSignOutUrl}
            appearance={clerkAppearance}
          >
            {children}
          </ClerkProvider>
        ) : children}
      </body>
    </html>
  );
}
