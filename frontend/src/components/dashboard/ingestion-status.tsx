interface IngestionStatusProps {
  active: boolean;
  message?: string | null;
}

export function IngestionStatus({ active, message }: IngestionStatusProps) {
  if (!active) {
    return null;
  }

  return (
    <div className="flex items-center gap-3 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-4 py-3 text-sm text-indigo-200" role="status" aria-live="polite">
      <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" aria-hidden="true" />
      <span>{message ?? "Ingestion is running. Repository cards refresh automatically."}</span>
    </div>
  );
}
