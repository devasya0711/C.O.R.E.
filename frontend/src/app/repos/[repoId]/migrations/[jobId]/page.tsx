import { MigrationOrchestrator } from "@/components/migrations/migration-orchestrator";
import { WorkspaceShell } from "@/components/workspace/workspace-shell";

interface MigrationPageProps {
  params: Promise<{ repoId: string; jobId: string }>;
}

export default async function MigrationPage({ params }: MigrationPageProps) {
  const { repoId, jobId } = await params;
  return <WorkspaceShell><MigrationOrchestrator repositoryId={repoId} jobId={jobId} /></WorkspaceShell>;
}
