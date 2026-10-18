import { SignUp } from "@clerk/nextjs";
import type { Metadata } from "next";

import { ConfigurationNotice } from "@/components/auth/configuration-notice";
import { getClerkConfiguration } from "@/lib/auth/config";

export const metadata: Metadata = { title: "Sign up | C.O.R.E." };
export const dynamic = "force-dynamic";

export default function SignUpPage() {
  const configuration = getClerkConfiguration();

  if (!configuration) {
    return <ConfigurationNotice />;
  }

  return (
    <SignUp
      routing="path"
      path={configuration.signUpUrl}
      signInUrl={configuration.signInUrl}
      fallbackRedirectUrl={configuration.signUpFallbackRedirectUrl}
    />
  );
}
