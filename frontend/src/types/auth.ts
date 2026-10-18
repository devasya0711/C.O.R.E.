import type { ReactNode } from "react";

export interface ClerkEnvironment {
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?: string;
  CLERK_SECRET_KEY?: string;
  NEXT_PUBLIC_CLERK_SIGN_IN_URL?: string;
  NEXT_PUBLIC_CLERK_SIGN_UP_URL?: string;
  NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL?: string;
  NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL?: string;
  NEXT_PUBLIC_CLERK_AFTER_SIGN_OUT_URL?: string;
}

export interface ClerkConfiguration {
  publishableKey: string;
  secretKey: string;
  signInUrl: string;
  signUpUrl: string;
  signInFallbackRedirectUrl: string;
  signUpFallbackRedirectUrl: string;
  afterSignOutUrl: string;
}

export interface LayoutChildrenProps {
  children: ReactNode;
}
