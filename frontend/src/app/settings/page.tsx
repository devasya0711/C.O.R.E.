import { SettingsPanel } from "@/components/settings/settings-panel";
import { WorkspaceShell } from "@/components/workspace/workspace-shell";

export default function SettingsPage() {
  return (
    <WorkspaceShell>
      <SettingsPanel />
    </WorkspaceShell>
  );
}
