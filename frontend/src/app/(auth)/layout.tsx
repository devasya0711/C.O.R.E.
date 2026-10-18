import Link from "next/link";

import type { LayoutChildrenProps } from "@/types/auth";

export default function AuthenticationLayout({ children }: Readonly<LayoutChildrenProps>) {
  return (
    <main className="flex min-h-screen flex-col bg-canvas px-4 py-6 text-primaryText sm:px-6">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 border-b border-hairline pb-5">
        <Link href="/" aria-label="C.O.R.E. home" className="flex items-center gap-3">
          <span className="grid h-8 w-8 place-items-center rounded-md border border-active bg-active/15 font-mono text-xs font-bold text-indigo-300">
            C
          </span>
          <span className="font-mono text-sm font-semibold tracking-[0.18em]">C.O.R.E.</span>
        </Link>
        <Link href="/" className="text-sm text-secondaryText hover:text-primaryText">
          Back to overview
        </Link>
      </header>

      <div className="mx-auto grid w-full max-w-6xl flex-1 items-center gap-10 py-12 lg:grid-cols-2 lg:gap-24">
        <section className="mx-auto w-full max-w-md">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-indigo-300">
            Identity / Access
          </p>
          <h1 className="mt-4 text-2xl font-semibold leading-8 tracking-[-0.015em]">
            Your identity. Your codebase.
          </h1>
          <p className="mt-4 text-sm leading-6 text-secondaryText">
            Use your C.O.R.E. account to establish a secure session. Authentication
            and account recovery are managed by Clerk.
          </p>
          <p className="mt-6 border-t border-hairline pt-4 font-mono text-xs leading-5 text-secondaryText">
            Codebase Orchestration &amp; Refactoring Engine
          </p>
        </section>

        <div className="mx-auto w-full max-w-md">{children}</div>
      </div>

      <footer className="mx-auto w-full max-w-6xl border-t border-hairline pt-4 text-xs text-secondaryText">
        Session authentication is required for workspace routes.
      </footer>
    </main>
  );
}
