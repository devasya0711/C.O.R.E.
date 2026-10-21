import { WorkspaceShell } from "@/components/workspace/workspace-shell";

export default function MigrationLoading() {
  return <WorkspaceShell><div className="mx-auto max-w-7xl animate-pulse space-y-6"><div className="h-8 w-80 rounded bg-surface" /><div className="grid gap-4 lg:grid-cols-2"><div className="h-72 rounded-lg border border-hairline bg-panel" /><div className="h-72 rounded-lg border border-hairline bg-panel" /></div></div></WorkspaceShell>;
}
