import { WorkspaceShell } from "@/components/workspace/workspace-shell";

export default function DashboardLoading() {
  return (
    <WorkspaceShell>
      <div className="mx-auto max-w-7xl animate-pulse space-y-8">
        <div className="h-8 w-64 rounded bg-surface" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {["one", "two", "three"].map((key) => (
            <div key={key} className="h-52 rounded-lg border border-hairline bg-panel" />
          ))}
        </div>
      </div>
    </WorkspaceShell>
  );
}
