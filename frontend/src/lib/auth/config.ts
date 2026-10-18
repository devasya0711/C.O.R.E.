import type { ClerkConfiguration, ClerkEnvironment } from "@/types/auth";

/** Presence check only: Clerk validates keys and verifies sessions at request time. */
export function getClerkConfiguration(
  environment: ClerkEnvironment = {
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
    CLERK_SECRET_KEY: process.env.CLERK_SECRET_KEY,
    NEXT_PUBLIC_CLERK_SIGN_IN_URL: process.env.NEXT_PUBLIC_CLERK_SIGN_IN_URL,
    NEXT_PUBLIC_CLERK_SIGN_UP_URL: process.env.NEXT_PUBLIC_CLERK_SIGN_UP_URL,
    NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL:
      process.env.NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL,
    NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL:
      process.env.NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL,
    NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL: process.env.NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL,
  },
): ClerkConfiguration | null {
  const publishableKey = environment.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim();
  const secretKey = environment.CLERK_SECRET_KEY?.trim();

  if (!publishableKey || !secretKey) {
    return null;
  }

  return {
    publishableKey,
    secretKey,
    signInUrl: environment.NEXT_PUBLIC_CLERK_SIGN_IN_URL?.trim() || "/sign-in",
    signUpUrl: environment.NEXT_PUBLIC_CLERK_SIGN_UP_URL?.trim() || "/sign-up",
    signInFallbackRedirectUrl:
      environment.NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL?.trim() || "/",
    signUpFallbackRedirectUrl:
      environment.NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL?.trim() || "/",
    afterSignOutUrl: environment.NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL?.trim() || "/",
  };
}
