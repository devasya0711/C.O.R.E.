import { GraphStudio } from "@/components/graph/graph-studio";
import { WorkspaceShell } from "@/components/workspace/workspace-shell";

interface GraphPageProps {
  params: Promise<{ repoId: string }>;
}

export default async function GraphPage({ params }: GraphPageProps) {
  const { repoId } = await params;
  return (
    <WorkspaceShell fullBleed>
      <GraphStudio repositoryId={repoId} />
    </WorkspaceShell>
  );
}
