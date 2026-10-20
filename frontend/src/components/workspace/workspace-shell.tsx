import Link from "next/link";
import type { PropsWithChildren } from "react";

import { AccountNavigation } from "@/components/auth/account-navigation";

const navigation = [
  { href: "/dashboard", label: "Repositories", marker: "REPO" },
];

export function WorkspaceShell({ children }: PropsWithChildren) {
  return (
    <div className="min-h-screen bg-canvas text-primaryText">
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-hairline bg-panel lg:block">
        <div className="flex h-full flex-col">
          <div className="flex h-14 items-center gap-3 border-b border-hairline px-5">
            <span className="grid h-7 w-7 place-items-center rounded-md border border-active bg-active/15 font-mono text-xs font-bold text-indigo-300">
              C
            </span>
            <div>
              <p className="font-mono text-xs font-semibold tracking-[0.18em]">C.O.R.E.</p>
              <p className="font-mono text-[9px] tracking-[0.14em] text-mutedText">WORKSPACE</p>
            </div>
          </div>

          <nav aria-label="Workspace" className="flex-1 px-3 py-5">
            <p className="px-3 font-mono text-[10px] uppercase tracking-[0.16em] text-mutedText">
              Navigation
            </p>
            <div className="mt-3 space-y-1">
              {navigation.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="group flex items-center gap-3 rounded-md px-3 py-2.5 text-sm text-secondaryText transition hover:bg-surface hover:text-primaryText"
                >
                  <span className="font-mono text-[9px] text-mutedText group-hover:text-indigo-300">
                    {item.marker}
                  </span>
                  {item.label}
                </Link>
              ))}
            </div>
          </nav>

          <div className="border-t border-hairline p-4">
            <p className="font-mono text-[10px] leading-5 text-mutedText">
              GRAPH REFACTORING SYSTEM
              <br />
              v0.1 / PHASE 6
            </p>
          </div>
        </div>
      </aside>

      <div className="lg:pl-60">
        <header className="flex h-14 items-center justify-between border-b border-hairline bg-canvas/95 px-5 backdrop-blur lg:px-8">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-mutedText">
              Codebase intelligence
            </p>
            <p className="text-sm font-medium text-primaryText">Repository workspace</p>
          </div>
          <AccountNavigation />
        </header>
        <main className="min-h-[calc(100vh-3.5rem)] px-5 py-8 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
