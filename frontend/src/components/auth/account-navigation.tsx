import { UserButton } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import Link from "next/link";

import { getClerkConfiguration } from "@/lib/auth/config";

export async function AccountNavigation() {
  const configuration = getClerkConfiguration();

  if (configuration) {
    const { userId } = await auth();

    if (userId) {
      return (
        <nav aria-label="Account" className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="rounded-md border border-hairline bg-panel px-3 py-2 text-xs font-medium text-secondaryText hover:bg-surface hover:text-primaryText"
          >
            Workspace
          </Link>
          <span className="hidden font-mono text-xs text-secondaryText sm:inline">Account</span>
          <UserButton />
        </nav>
      );
    }
  }

  return (
    <nav aria-label="Account" className="flex items-center gap-4 text-sm">
      <Link href={configuration?.signInUrl ?? "/sign-in"} className="text-secondaryText hover:text-primaryText">
        Sign in
      </Link>
      <Link
        href={configuration?.signUpUrl ?? "/sign-up"}
        className="rounded-md border border-hairline bg-panel px-3 py-2 font-medium hover:bg-surface"
      >
        Sign up
      </Link>
    </nav>
  );
}
