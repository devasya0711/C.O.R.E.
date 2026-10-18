import { SignIn } from "@clerk/nextjs";
import type { Metadata } from "next";

import { ConfigurationNotice } from "@/components/auth/configuration-notice";
import { getClerkConfiguration } from "@/lib/auth/config";

export const metadata: Metadata = { title: "Sign in | C.O.R.E." };
export const dynamic = "force-dynamic";

export default function SignInPage() {
  const configuration = getClerkConfiguration();

  if (!configuration) {
    return <ConfigurationNotice />;
  }

  return (
    <SignIn
      routing="path"
      path={configuration.signInUrl}
      signUpUrl={configuration.signUpUrl}
      fallbackRedirectUrl={configuration.signInFallbackRedirectUrl}
    />
  );
}
