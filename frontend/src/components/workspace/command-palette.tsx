"use client";

import { useAuth } from "@clerk/nextjs";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { getRepositoryGraph, listRepositories } from "@/lib/api/repositories";
import type { GraphNode } from "@/types/graph";
import type { RepositorySummary } from "@/types/repositories";

interface CommandItem {
  id: string;
  label: string;
  description: string;
  category: "Navigation" | "Repositories" | "Graph nodes" | "Actions";
  href?: string;
  action?: "extract";
}

function currentRepositoryId(pathname: string): string | null {
  const match = pathname.match(/^\/repos\/([^/]+)\/graph/u);
  return match?.[1] ?? null;
}

export function CommandPalette() {
  const { getToken } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState(0);
  const [repositories, setRepositories] = useState<RepositorySummary[]>([]);
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);

  const repositoryId = currentRepositoryId(pathname);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelection(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    let cancelled = false;
    async function loadCommands(): Promise<void> {
      setLoading(true);
      try {
        const token = await getToken();
        if (!token) return;
        const nextRepositories = await listRepositories(token);
        if (!cancelled) setRepositories(nextRepositories);
        if (repositoryId) {
          const response = await getRepositoryGraph(token, repositoryId);
          if (!cancelled) setGraphNodes(response.nodes);
        } else if (!cancelled) {
          setGraphNodes([]);
        }
      } catch {
        if (!cancelled) setGraphNodes([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadCommands();
    return () => { cancelled = true; };
  }, [getToken, open, repositoryId]);

  const items = useMemo<CommandItem[]>(() => {
    const base: CommandItem[] = [
      { id: "dashboard", label: "Open repositories", description: "Return to the workspace dashboard", category: "Navigation", href: "/dashboard" },
      { id: "settings", label: "Open settings", description: "Manage GitHub integration", category: "Navigation", href: "/settings" },
    ];
    if (repositoryId) {
      base.push({ id: "extract-seam", label: "Extract seam", description: "Analyze communities on the current graph", category: "Actions", action: "extract" });
    }
    base.push(...repositories.map((repository) => ({
      id: `repo:${repository.id}`,
      label: repository.name,
      description: `${repository.status} · ${repository.repo_url}`,
      category: "Repositories" as const,
      href: repository.status === "READY" ? `/repos/${repository.id}/graph` : "/dashboard",
    })));
    base.push(...graphNodes.map((node) => ({
      id: `node:${node.id}`,
      label: node.data.qualified_name,
      description: `${node.data.label} · ${node.data.file_path}`,
      category: "Graph nodes" as const,
      href: repositoryId ? `/repos/${repositoryId}/graph?node=${encodeURIComponent(node.id)}` : undefined,
    })));
    const normalized = query.trim().toLowerCase();
    if (!normalized) return base;
    return base.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(normalized));
  }, [graphNodes, query, repositories, repositoryId]);

  function choose(item: CommandItem): void {
    if (item.action === "extract" && repositoryId) {
      setOpen(false);
      router.push(`/repos/${repositoryId}/graph?analyze=1`);
      return;
    }
    if (item.href) {
      setOpen(false);
      router.push(item.href);
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelection((current) => Math.min(current + 1, Math.max(items.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelection((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = items[selection];
      if (item) choose(item);
    }
  }

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="hidden rounded-md border border-hairline bg-panel px-2.5 py-1.5 font-mono text-[10px] text-mutedText hover:bg-surface hover:text-secondaryText md:block" aria-label="Open command palette">
        ⌘K <span className="ml-1">COMMANDS</span>
      </button>
      {open ? (
        <div className="fixed inset-0 z-[60] bg-black/70 px-5 pt-[12vh]" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <section role="dialog" aria-modal="true" aria-labelledby="command-palette-title" className="mx-auto w-full max-w-2xl overflow-hidden rounded-lg border border-hairline bg-panel shadow-2xl shadow-black/50">
            <h2 id="command-palette-title" className="sr-only">Command palette</h2>
            <div className="border-b border-hairline p-3">
              <label htmlFor="command-search" className="sr-only">Search commands</label>
              <input ref={inputRef} id="command-search" value={query} onChange={(event) => { setQuery(event.target.value); setSelection(0); }} onKeyDown={handleInputKeyDown} placeholder="Jump to a repository, search a symbol, or extract a seam…" className="w-full bg-transparent px-2 py-2 text-sm text-primaryText outline-none placeholder:text-mutedText" />
            </div>
            <div className="max-h-[55vh] overflow-y-auto p-2">
              {loading ? <p className="px-3 py-4 font-mono text-xs text-mutedText">Loading workspace commands…</p> : null}
              {!loading && items.length === 0 ? <p className="px-3 py-4 text-sm text-secondaryText">No commands match this search.</p> : null}
              {!loading ? items.map((item, index) => (
                <button key={item.id} type="button" onClick={() => choose(item)} onMouseEnter={() => setSelection(index)} className={`flex w-full items-start gap-3 rounded-md px-3 py-3 text-left ${selection === index ? "bg-surface" : "hover:bg-surface/70"}`}>
                  <span className="mt-0.5 w-24 shrink-0 font-mono text-[9px] uppercase tracking-[0.12em] text-mutedText">{item.category}</span>
                  <span className="min-w-0"><span className="block truncate text-sm text-primaryText">{item.label}</span><span className="mt-1 block truncate text-xs text-secondaryText">{item.description}</span></span>
                </button>
              )) : null}
            </div>
            <div className="flex justify-between border-t border-hairline px-4 py-2 font-mono text-[10px] text-mutedText"><span>↑↓ navigate · Enter select</span><span>Esc close</span></div>
          </section>
        </div>
      ) : null}
    </>
  );
}
