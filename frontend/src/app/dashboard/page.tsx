import { WorkspaceShell } from "@/components/workspace/workspace-shell";
import { RepositoryDashboard } from "@/components/dashboard/repository-dashboard";

export default function DashboardPage() {
  return (
    <WorkspaceShell>
      <RepositoryDashboard />
    </WorkspaceShell>
  );
}
