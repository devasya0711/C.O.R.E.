import { WorkspaceShell } from "@/components/workspace/workspace-shell";

export default function GraphLoading() {
  return (
    <WorkspaceShell fullBleed>
      <div className="grid h-[calc(100vh-3.5rem)] place-items-center bg-canvas [background-image:radial-gradient(#1F2430_1px,transparent_1px)] [background-size:18px_18px]">
        <div className="rounded-full border border-hairline bg-panel px-4 py-2 font-mono text-xs text-secondaryText">
          Loading graph workspace…
        </div>
      </div>
    </WorkspaceShell>
  );
}
