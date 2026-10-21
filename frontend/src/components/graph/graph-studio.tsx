"use client";

import "@xyflow/react/dist/style.css";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClientError } from "@/lib/api/client";
import { analyzeImpact, analyzeSeams } from "@/lib/api/analysis";
import { startMigration } from "@/lib/api/migrations";
import { getRepositoryGraph, listRepositories } from "@/lib/api/repositories";
import type { ImpactResponse, SeamSummary } from "@/types/analysis";
import type {
  GraphEdge,
  GraphEdgeData,
  GraphNode,
  GraphNodeData,
  GraphNodeLabel,
  GraphResponse,
  GraphRelationship,
} from "@/types/graph";

type StudioNodeData = GraphNodeData & { showCommunity: boolean };
type StudioNode = Node<StudioNodeData, "codeNode">;
type StudioEdge = Edge<GraphEdgeData>;
type NodeFilter = "ALL" | GraphNodeLabel;
type RelationshipFilter = "ALL" | GraphRelationship;
type LoadState = "loading" | "ready" | "access" | "error";

const nodeTypes = { codeNode: CodeNode };
const nodeLabels: GraphNodeLabel[] = ["File", "Class", "Function", "APIEndpoint", "DBTable"];
const relationships: GraphRelationship[] = [
  "CONTAINS",
  "CALLS",
  "INHERITS",
  "READS_FROM",
  "WRITES_TO",
  "ROUTES_TO",
];

const labelColors: Record<GraphNodeLabel, string> = {
  File: "#6B7280",
  Class: "#8B5CF6",
  Function: "#3B82F6",
  APIEndpoint: "#10B981",
  DBTable: "#F59E0B",
};

const communityColors = ["#4F46E5", "#06B6D4", "#EC4899", "#84CC16", "#F97316"];

function communityColor(communityId: number | null): string {
  if (communityId === null) {
    return labelColors.File;
  }
  return communityColors[Math.abs(communityId) % communityColors.length];
}

function nodeBadge(label: GraphNodeLabel): string {
  const badges: Record<GraphNodeLabel, string> = {
    File: "FILE",
    Class: "CLS",
    Function: "FN",
    APIEndpoint: "API",
    DBTable: "DB",
  };
  return badges[label];
}

function toStudioNodes(nodes: GraphNode[]): StudioNode[] {
  return nodes.map((node) => ({
    ...node,
    data: { ...node.data, showCommunity: true },
  }));
}

function toStudioEdges(edges: GraphEdge[]): StudioEdge[] {
  return edges.map((edge) => ({
    ...edge,
    data: edge.data,
  }));
}

export function GraphStudio({ repositoryId }: { repositoryId: string }) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [repositorySource, setRepositorySource] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<StudioEdge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>("ALL");
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("ALL");
  const [search, setSearch] = useState("");
  const [showCommunities, setShowCommunities] = useState(true);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [seams, setSeams] = useState<SeamSummary[]>([]);
  const [seamPanelOpen, setSeamPanelOpen] = useState(false);
  const [seamsLoading, setSeamsLoading] = useState(false);
  const [seamError, setSeamError] = useState<string | null>(null);
  const [migrationSeamId, setMigrationSeamId] = useState<string | null>(null);
  const [createGithubPr, setCreateGithubPr] = useState(false);
  const [impact, setImpact] = useState<ImpactResponse | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState<string | null>(null);
  const [impactHops, setImpactHops] = useState(3);
  const [changeSummary, setChangeSummary] = useState("");
  const [autoAnalyzeStarted, setAutoAnalyzeStarted] = useState(false);

  const loadGraph = useCallback(async (signal?: AbortSignal) => {
    if (!isLoaded || !isSignedIn) {
      return;
    }
    setLoadState("loading");
    setError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new ApiClientError(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");
      }
      const response = await getRepositoryGraph(token, repositoryId, signal);
      try {
        const repositories = await listRepositories(token, signal);
        setRepositorySource(repositories.find((repository) => repository.id === repositoryId)?.repo_url ?? null);
      } catch {
        setRepositorySource(null);
      }
      setGraph(response);
      setNodes(toStudioNodes(response.nodes));
      setEdges(toStudioEdges(response.edges));
      setSelectedNodeId(null);
      setLoadState("ready");
    } catch (caught: unknown) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        return;
      }
      setError({
        code: caught instanceof ApiClientError ? caught.code : "GRAPH_LOAD_FAILED",
        message: caught instanceof Error ? caught.message : "The architecture graph could not be loaded.",
      });
      setLoadState("error");
    }
  }, [getToken, isLoaded, isSignedIn, repositoryId, setEdges, setNodes]);

  useEffect(() => {
    if (!isLoaded) {
      setLoadState("loading");
      return;
    }
    if (!isSignedIn) {
      setLoadState("access");
      return;
    }
    const controller = new AbortController();
    void loadGraph(controller.signal);
    return () => controller.abort();
  }, [isLoaded, isSignedIn, loadGraph]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotionPreference = (): void => setPrefersReducedMotion(mediaQuery.matches);
    updateMotionPreference();
    mediaQuery.addEventListener("change", updateMotionPreference);
    return () => mediaQuery.removeEventListener("change", updateMotionPreference);
  }, []);

  const runSeamAnalysis = useCallback(async () => {
    setSeamsLoading(true);
    setSeamError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new ApiClientError(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");
      }
      const result = await analyzeSeams(token, repositoryId);
      setSeams(result.seams);
      setSeamPanelOpen(true);
    } catch (caught: unknown) {
      setSeamError(caught instanceof Error ? caught.message : "Seam analysis could not be completed.");
    } finally {
      setSeamsLoading(false);
    }
  }, [getToken, repositoryId]);

  useEffect(() => {
    if (loadState === "ready" && searchParams.get("analyze") === "1" && !autoAnalyzeStarted) {
      setAutoAnalyzeStarted(true);
      void runSeamAnalysis();
    }
  }, [autoAnalyzeStarted, loadState, runSeamAnalysis, searchParams]);

  useEffect(() => {
    const nodeId = searchParams.get("node");
    if (nodeId && nodes.some((node) => node.id === nodeId)) {
      setSelectedNodeId(nodeId);
    }
  }, [nodes, searchParams]);

  const runImpactAnalysis = useCallback(async () => {
    const selected = nodes.find((node) => node.id === selectedNodeId);
    if (!selected || !["Class", "Function"].includes(selected.data.label)) {
      setImpactError("Impact analysis requires a Function or Class target.");
      return;
    }
    setImpactLoading(true);
    setImpactError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new ApiClientError(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");
      }
      const result = await analyzeImpact(token, repositoryId, {
        target_qualified_name: selected.data.qualified_name,
        max_hops: impactHops,
        proposed_change_summary: changeSummary.trim(),
      });
      setImpact(result);
    } catch (caught: unknown) {
      setImpactError(caught instanceof Error ? caught.message : "Blast-radius analysis could not be completed.");
    } finally {
      setImpactLoading(false);
    }
  }, [changeSummary, getToken, impactHops, nodes, repositoryId, selectedNodeId]);

  const beginMigration = useCallback(async (seam: SeamSummary): Promise<void> => {
    if (createGithubPr && !repositorySource?.startsWith("https://github.com/")) {
      setSeamError("GitHub PR creation requires a GitHub repository source.");
      return;
    }
    setMigrationSeamId(seam.id);
    setSeamError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new ApiClientError(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");
      }
      const accepted = await startMigration(token, repositoryId, seam.id, createGithubPr);
      router.push(`/repos/${repositoryId}/migrations/${accepted.job_id}`);
    } catch (caught: unknown) {
      setSeamError(caught instanceof Error ? caught.message : "Migration could not be started.");
    } finally {
      setMigrationSeamId(null);
    }
  }, [createGithubPr, getToken, repositoryId, repositorySource, router]);

  const filteredNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return nodes.filter((node) => {
      const matchesType = nodeFilter === "ALL" || node.data.label === nodeFilter;
      const matchesSearch =
        !query ||
        node.data.name.toLowerCase().includes(query) ||
        node.data.qualified_name.toLowerCase().includes(query) ||
        node.data.file_path.toLowerCase().includes(query);
      return matchesType && matchesSearch;
    });
  }, [nodeFilter, nodes, search]);

  const filteredEdges = useMemo(() => {
    const visibleIds = new Set(filteredNodes.map((node) => node.id));
    return edges.filter(
      (edge) =>
        (relationshipFilter === "ALL" || edge.label === relationshipFilter) &&
        visibleIds.has(edge.source) &&
        visibleIds.has(edge.target),
    );
  }, [edges, filteredNodes, relationshipFilter]);

  const nodeIdsByQualifiedName = useMemo(
    () => new Map(nodes.map((node) => [node.data.qualified_name, node.id])),
    [nodes],
  );
  const affectedNodeIds = useMemo(() => {
    if (!impact) return null;
    return new Set(
      [impact.target, ...impact.affected_nodes]
        .map((qualifiedName) => nodeIdsByQualifiedName.get(qualifiedName))
        .filter((id): id is string => Boolean(id)),
    );
  }, [impact, nodeIdsByQualifiedName]);

  const visibleNodes = useMemo(
    () =>
      filteredNodes.map((node) => ({
        ...node,
        data: { ...node.data, showCommunity: showCommunities },
        style: {
          opacity: affectedNodeIds
            ? affectedNodeIds.has(node.id) ? 1 : 0.25
            : selectedNodeId && selectedNodeId !== node.id ? 0.25 : 1,
          transition: "opacity 150ms ease",
        },
      })),
    [affectedNodeIds, filteredNodes, selectedNodeId, showCommunities],
  );

  const visibleEdges = useMemo(
    () =>
      filteredEdges.map((edge) => {
        const relatedToSelection =
          selectedNodeId !== null &&
          (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const affectedByImpact = impact?.affected_edges.some(
          (affectedEdge) =>
            affectedEdge.source === edge.data?.source && affectedEdge.target === edge.data?.target,
        ) ?? false;
        return {
          ...edge,
          animated: (relatedToSelection || affectedByImpact) && !prefersReducedMotion,
          style: {
            stroke: relatedToSelection || affectedByImpact ? "#EF4444" : "#4B5563",
            strokeWidth: relatedToSelection || affectedByImpact ? 2 : 1,
          },
          labelStyle: { fill: "#9CA3AF", fontSize: 9, fontFamily: "monospace" },
        };
      }),
    [filteredEdges, impact, prefersReducedMotion, selectedNodeId],
  );

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const impactSupported = selectedNode !== null && ["Class", "Function"].includes(selectedNode.data.label);
  const githubRepositoryAvailable = repositorySource?.startsWith("https://github.com/") ?? false;

  if (loadState === "loading") {
    return <GraphLoading />;
  }
  if (loadState === "access") {
    return <GraphMessage title="Sign-in required" message="Sign in to inspect this repository graph." />;
  }
  if (loadState === "error") {
    return (
      <GraphMessage
        code={error?.code}
        title={error?.code === "REPOSITORY_NOT_READY" ? "Repository is still ingesting" : "Graph unavailable"}
        message={error?.message ?? "The architecture graph could not be loaded."}
        onRetry={() => void loadGraph()}
      />
    );
  }

  return (
    <div className="relative h-[calc(100vh-3.5rem)] min-h-[600px] overflow-hidden bg-canvas">
      <div className="absolute left-4 top-4 z-10 w-[min(440px,calc(100%-2rem))] rounded-lg border border-hairline bg-panel/95 p-3 shadow-2xl shadow-black/30 backdrop-blur">
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/dashboard" className="rounded-md border border-hairline px-2.5 py-2 font-mono text-[10px] text-secondaryText hover:bg-surface hover:text-primaryText">
            ← REPOS
          </Link>
          <span className="font-mono text-xs text-mutedText">GRAPH STUDIO</span>
          <button type="button" onClick={() => void runSeamAnalysis()} disabled={seamsLoading} className="rounded-md border border-indigo-500/40 px-2.5 py-2 font-mono text-[10px] text-indigo-300 hover:bg-indigo-500/10 disabled:cursor-wait disabled:opacity-50">
            {seamsLoading ? "ANALYZING…" : "ANALYZE SEAMS"}
          </button>
          <span className="ml-auto font-mono text-[10px] text-mutedText">
            {filteredNodes.length}/{nodes.length} NODES
          </span>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <label className="sr-only" htmlFor="graph-search">Search graph nodes</label>
          <input
            id="graph-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search symbols or files"
            className="rounded-md border border-hairline bg-surface px-3 py-2 text-xs text-primaryText placeholder:text-mutedText sm:col-span-2"
          />
          <label className="sr-only" htmlFor="node-filter">Filter node type</label>
          <select id="node-filter" value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value as NodeFilter)} className="rounded-md border border-hairline bg-surface px-3 py-2 text-xs text-secondaryText">
            <option value="ALL">All node types</option>
            {nodeLabels.map((label) => <option key={label} value={label}>{label}</option>)}
          </select>
          <label className="sr-only" htmlFor="relationship-filter">Filter relationship</label>
          <select id="relationship-filter" value={relationshipFilter} onChange={(event) => setRelationshipFilter(event.target.value as RelationshipFilter)} className="rounded-md border border-hairline bg-surface px-3 py-2 text-xs text-secondaryText">
            <option value="ALL">All relationships</option>
            {relationships.map((relationship) => <option key={relationship} value={relationship}>{relationship}</option>)}
          </select>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-3">
          <label className="flex items-center gap-2 text-xs text-secondaryText">
            <input type="checkbox" checked={showCommunities} onChange={(event) => setShowCommunities(event.target.checked)} className="accent-indigo-500" />
            Show community rings
          </label>
          <button type="button" onClick={() => void loadGraph()} className="rounded-md border border-hairline px-3 py-1.5 font-mono text-[10px] text-secondaryText hover:bg-surface hover:text-primaryText">
            Refresh graph
          </button>
        </div>
      </div>

      {seamPanelOpen ? (
        <SeamPanel
          seams={seams}
          error={seamError}
          createGithubPr={createGithubPr}
          canCreateGithubPr={githubRepositoryAvailable}
          migrationSeamId={migrationSeamId}
          onCreateGithubPrChange={setCreateGithubPr}
          onStartMigration={(seam) => void beginMigration(seam)}
          onClose={() => setSeamPanelOpen(false)}
        />
      ) : null}

      {graph?.nodes.length === 0 ? (
        <EmptyGraph />
      ) : (
        <ReactFlow
          nodes={visibleNodes}
          edges={visibleEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, node) => setSelectedNodeId(node.id)}
          onPaneClick={() => setSelectedNodeId(null)}
          fitView
          minZoom={0.15}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} color="#1F2430" gap={18} size={1} />
          <Controls className="!border-hairline !bg-panel !fill-primaryText" />
          <MiniMap
            className="!border-hairline !bg-panel"
            nodeColor={(node) => {
              const nodeData = node.data as StudioNodeData;
              return nodeData.showCommunity
                ? communityColor(nodeData.community_id)
                : labelColors[nodeData.label];
            }}
          />
        </ReactFlow>
      )}

      {graph?.nodes.length !== 0 ? <NodeListFallback nodes={filteredNodes} onSelect={setSelectedNodeId} /> : null}
      <Inspector
        node={selectedNode}
        impact={impact}
        impactLoading={impactLoading}
        impactError={impactError}
        impactSupported={impactSupported}
        impactHops={impactHops}
        changeSummary={changeSummary}
        onImpactHopsChange={setImpactHops}
        onChangeSummary={setChangeSummary}
        onRunImpact={() => void runImpactAnalysis()}
      />
    </div>
  );
}

function CodeNode({ data, selected }: NodeProps<StudioNode>) {
  const ringColor = data.showCommunity && data.community_id !== null
    ? communityColor(data.community_id)
    : labelColors[data.label];
  return (
    <div className="min-w-48 max-w-64 rounded-md border bg-panel px-3 py-2 shadow-lg shadow-black/25" style={{ borderColor: ringColor, boxShadow: selected ? `0 0 0 2px ${ringColor}55` : undefined }}>
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-secondaryText" />
      <div className="flex items-center justify-between gap-3">
        <span className="rounded border px-1.5 py-0.5 font-mono text-[9px]" style={{ borderColor: ringColor, color: ringColor }}>{nodeBadge(data.label)}</span>
        {data.community_id !== null ? <span className="font-mono text-[9px] text-mutedText">C{data.community_id}</span> : null}
      </div>
      <p className="mt-2 truncate font-mono text-xs font-semibold text-primaryText" title={data.qualified_name}>{data.name}</p>
      <p className="mt-1 truncate font-mono text-[10px] text-mutedText" title={data.file_path}>{data.file_path}:{data.start_line}</p>
      <div className="mt-2 flex gap-1.5 font-mono text-[9px] text-secondaryText">
        <span className="rounded bg-surface px-1.5 py-0.5">↓ {data.incoming_count}</span>
        <span className="rounded bg-surface px-1.5 py-0.5">↑ {data.outgoing_count}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-secondaryText" />
    </div>
  );
}

interface InspectorProps {
  node: StudioNode | null;
  impact: ImpactResponse | null;
  impactLoading: boolean;
  impactError: string | null;
  impactSupported: boolean;
  impactHops: number;
  changeSummary: string;
  onImpactHopsChange: (hops: number) => void;
  onChangeSummary: (summary: string) => void;
  onRunImpact: () => void;
}

function Inspector({
  node,
  impact,
  impactLoading,
  impactError,
  impactSupported,
  impactHops,
  changeSummary,
  onImpactHopsChange,
  onChangeSummary,
  onRunImpact,
}: InspectorProps) {
  return (
    <aside aria-label="Node inspector" className={`absolute bottom-0 left-0 right-0 z-10 max-h-[45vh] overflow-y-auto border-t border-hairline bg-panel/95 p-5 shadow-2xl shadow-black/30 backdrop-blur lg:bottom-4 lg:left-auto lg:right-4 lg:top-4 lg:w-[380px] lg:max-h-none lg:rounded-lg lg:border ${node ? "block" : "hidden lg:block"}`}>
      <InspectorContent
        node={node}
        impact={impact}
        impactLoading={impactLoading}
        impactError={impactError}
        impactSupported={impactSupported}
        impactHops={impactHops}
        changeSummary={changeSummary}
        onImpactHopsChange={onImpactHopsChange}
        onChangeSummary={onChangeSummary}
        onRunImpact={onRunImpact}
      />
    </aside>
  );
}

function InspectorContent({
  node,
  impact,
  impactLoading,
  impactError,
  impactSupported,
  impactHops,
  changeSummary,
  onImpactHopsChange,
  onChangeSummary,
  onRunImpact,
}: InspectorProps) {
  if (!node) {
    return (
      <div className="flex h-full min-h-72 flex-col justify-center">
        <p className="font-mono text-xs uppercase tracking-[0.16em] text-indigo-300">Node inspector</p>
        <h2 className="mt-3 text-lg font-semibold">Select a symbol</h2>
        <p className="mt-3 text-sm leading-6 text-secondaryText">Choose a node to inspect source location, relationships, community metadata, and read-only source context.</p>
      </div>
    );
  }
  const { data } = node;
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-indigo-300">Node inspector</p>
      <h2 className="mt-3 break-words text-lg font-semibold">{data.name}</h2>
      <p className="mt-2 break-all font-mono text-xs text-secondaryText">{data.qualified_name}</p>
      <div className="mt-5 grid grid-cols-2 gap-3 border-y border-hairline py-4">
        <InspectorMetric label="Type" value={data.label} />
        <InspectorMetric label="Community" value={data.community_id === null ? "—" : String(data.community_id)} />
        <InspectorMetric label="Incoming" value={String(data.incoming_count)} />
        <InspectorMetric label="Outgoing" value={String(data.outgoing_count)} />
      </div>
      <div className="mt-5 space-y-4 text-sm">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">Source location</p>
          <p className="mt-1 break-all font-mono text-xs text-secondaryText">{data.file_path}:{data.start_line}–{data.end_line}</p>
        </div>
        {data.shared_db_tables.length > 0 ? (
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-amber-300">Shared database tables</p>
            <div className="mt-2 flex flex-wrap gap-1.5">{data.shared_db_tables.map((table) => <span key={table} className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 font-mono text-[10px] text-amber-200">{table}</span>)}</div>
          </div>
        ) : null}
        {data.docstring ? <div><p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">Documentation</p><p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-secondaryText">{data.docstring}</p></div> : null}
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-red-300">Blast radius simulator</p>
          {impactSupported ? <>
            <label htmlFor="impact-summary" className="mt-3 block font-mono text-[10px] text-mutedText">Proposed change</label>
            <textarea id="impact-summary" value={changeSummary} onChange={(event) => onChangeSummary(event.target.value)} placeholder="Describe the signature or behavior change…" className="mt-1.5 min-h-16 w-full resize-y rounded-md border border-hairline bg-surface px-2.5 py-2 text-xs text-primaryText placeholder:text-mutedText" />
            <div className="mt-2 flex items-center gap-2">
              <label htmlFor="impact-hops" className="font-mono text-[10px] text-mutedText">Hops</label>
              <select id="impact-hops" value={impactHops} onChange={(event) => onImpactHopsChange(Number(event.target.value))} className="rounded-md border border-hairline bg-surface px-2 py-1.5 font-mono text-xs text-secondaryText">
                {[1, 2, 3, 4, 5].map((hops) => <option key={hops} value={hops}>{hops}</option>)}
              </select>
              <button type="button" onClick={onRunImpact} disabled={impactLoading} className="ml-auto rounded-md bg-red-600 px-2.5 py-1.5 text-[10px] font-semibold text-white hover:bg-red-500 disabled:cursor-wait disabled:opacity-50">
                {impactLoading ? "Running…" : "Simulate"}
              </button>
            </div>
            {impactError ? <p className="mt-2 text-xs text-red-300" role="alert">{impactError}</p> : null}
            {impact ? <ImpactResult impact={impact} /> : null}
          </> : <p className="mt-2 text-xs leading-5 text-secondaryText">Impact analysis requires a Function or Class target.</p>}
        </div>
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">Source</p>
          <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-hairline bg-surface p-3 font-mono text-[11px] leading-5 text-secondaryText"><code>{data.source_code || "No source text was returned for this node."}</code></pre>
        </div>
      </div>
    </div>
  );
}

function InspectorMetric({ label, value }: { label: string; value: string }) {
  return <div><p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">{label}</p><p className="mt-1 font-mono text-xs text-primaryText">{value}</p></div>;
}

function ImpactResult({ impact }: { impact: ImpactResponse }) {
  const riskColor = impact.risk_score >= 70 ? "text-red-300" : impact.risk_score >= 40 ? "text-amber-300" : "text-emerald-300";
  return (
    <div className="mt-3 border-t border-red-500/20 pt-3">
      <div className="flex items-end justify-between gap-3">
        <div><p className="font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">Risk score</p><p className={`mt-1 font-mono text-2xl font-semibold ${riskColor}`}>{impact.risk_score}<span className="text-xs text-mutedText">/100</span></p></div>
        <p className="font-mono text-[10px] text-mutedText">{impact.affected_nodes.length} nodes · {impact.affected_edges.length} edges</p>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface"><div className={`h-full ${impact.risk_score >= 70 ? "bg-red-500" : impact.risk_score >= 40 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${impact.risk_score}%` }} /></div>
      <p className="mt-3 text-xs leading-5 text-secondaryText">{impact.impact_summary}</p>
      {impact.affected_db_tables.length > 0 ? <p className="mt-2 font-mono text-[10px] text-amber-300">DB: {impact.affected_db_tables.join(", ")}</p> : null}
    </div>
  );
}

function SeamPanel({
  seams,
  error,
  createGithubPr,
  canCreateGithubPr,
  migrationSeamId,
  onCreateGithubPrChange,
  onStartMigration,
  onClose,
}: {
  seams: SeamSummary[];
  error: string | null;
  createGithubPr: boolean;
  canCreateGithubPr: boolean;
  migrationSeamId: string | null;
  onCreateGithubPrChange: (value: boolean) => void;
  onStartMigration: (seam: SeamSummary) => void;
  onClose: () => void;
}) {
  return (
    <section aria-label="Seam analysis results" className="absolute left-4 top-28 z-20 max-h-[calc(100%-9rem)] w-[min(520px,calc(100%-2rem))] overflow-y-auto rounded-lg border border-hairline bg-panel/95 p-4 shadow-2xl shadow-black/40 backdrop-blur">
      <div className="flex items-start justify-between gap-4"><div><p className="font-mono text-[10px] uppercase tracking-[0.16em] text-indigo-300">Boundary mapper</p><h2 className="mt-2 text-base font-semibold">Candidate seam clusters</h2></div><button type="button" onClick={onClose} aria-label="Close seam results" className="text-xl leading-none text-secondaryText hover:text-primaryText">×</button></div>
      {error ? <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200" role="alert">{error}</p> : null}
      {!error && seams.length === 0 ? <p className="mt-5 text-sm text-secondaryText">No active seams were returned for this graph.</p> : null}
      <div className="mt-4 space-y-3">{seams.map((seam) => <article key={seam.id} className="rounded-md border border-hairline bg-surface p-3"><div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-primaryText">{seam.suggested_name}</p><p className="mt-1 font-mono text-[10px] text-mutedText">Community {seam.community_id} · {seam.node_ids.length} nodes</p></div><span className="font-mono text-xs text-emerald-300">{Math.round(seam.cohesion_score * 100)}% cohesion</span></div>{seam.shared_db_tables.length > 0 ? <p className="mt-2 font-mono text-[10px] text-amber-300">Shared DB: {seam.shared_db_tables.join(", ")}</p> : null}<div className="mt-3 flex flex-wrap items-center justify-between gap-3"><label className="flex items-center gap-2 text-[11px] text-secondaryText"><input type="checkbox" checked={createGithubPr} disabled={!canCreateGithubPr} onChange={(event) => onCreateGithubPrChange(event.target.checked)} className="accent-indigo-500" /> Create GitHub PR</label><button type="button" onClick={() => onStartMigration(seam)} disabled={migrationSeamId !== null} className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-50">{migrationSeamId === seam.id ? "Starting…" : "Extract seam"}</button></div>{!canCreateGithubPr ? <p className="mt-2 text-[10px] text-mutedText">Automatic GitHub PRs are available only for GitHub repository sources.</p> : null}</article>)}</div>
    </section>
  );
}

function NodeListFallback({ nodes, onSelect }: { nodes: StudioNode[]; onSelect: (id: string) => void }) {
  return (
    <details className="absolute bottom-4 left-4 z-10 max-w-[min(360px,calc(100%-2rem))] rounded-lg border border-hairline bg-panel/95 shadow-2xl shadow-black/30 backdrop-blur">
      <summary className="cursor-pointer list-none px-3 py-2 font-mono text-[10px] text-secondaryText">KEYBOARD NODE LIST ({nodes.length})</summary>
      <div className="max-h-52 overflow-y-auto border-t border-hairline p-2">
        {nodes.map((node) => <button key={node.id} type="button" onClick={() => onSelect(node.id)} className="block w-full truncate rounded px-2 py-1.5 text-left font-mono text-[10px] text-secondaryText hover:bg-surface hover:text-primaryText">{node.data.qualified_name}</button>)}
      </div>
    </details>
  );
}

function EmptyGraph() {
  return (
    <div className="grid h-full place-items-center px-6 [background-image:radial-gradient(#1F2430_1px,transparent_1px)] [background-size:18px_18px]">
      <section className="max-w-lg rounded-lg border border-dashed border-hairline bg-panel/95 p-7 text-center shadow-2xl shadow-black/30">
        <p className="font-mono text-xs uppercase tracking-[0.18em] text-indigo-300">Graph is empty</p>
        <h1 className="mt-3 text-xl font-semibold">No architecture nodes were returned.</h1>
        <p className="mt-3 text-sm leading-6 text-secondaryText">Return to the repository workspace to retry ingestion or register another codebase.</p>
        <Link href="/dashboard" className="mt-6 inline-flex rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500">Back to repositories</Link>
      </section>
    </div>
  );
}

function GraphLoading() {
  return <div className="grid h-[calc(100vh-3.5rem)] place-items-center bg-canvas [background-image:radial-gradient(#1F2430_1px,transparent_1px)] [background-size:18px_18px]"><div className="rounded-full border border-hairline bg-panel px-4 py-2 font-mono text-xs text-secondaryText shadow-xl shadow-black/30" role="status" aria-live="polite"><span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-400" aria-hidden="true" />Loading architecture graph…</div></div>;
}

function GraphMessage({ code, title, message, onRetry }: { code?: string; title: string; message: string; onRetry?: () => void }) {
  return <div className="grid h-[calc(100vh-3.5rem)] place-items-center bg-canvas px-5"><section className="w-full max-w-lg rounded-lg border border-hairline bg-panel p-6"><p className="font-mono text-xs uppercase tracking-[0.16em] text-red-300">{code ?? "GRAPH_ERROR"}</p><h1 className="mt-3 text-xl font-semibold">{title}</h1><p className="mt-3 text-sm leading-6 text-secondaryText">{message}</p><div className="mt-5 flex flex-wrap gap-3">{onRetry ? <button type="button" onClick={onRetry} className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500">Retry graph</button> : null}<Link href="/dashboard" className="rounded-md border border-hairline px-4 py-2.5 text-sm text-secondaryText hover:bg-surface hover:text-primaryText">Back to repositories</Link></div></section></div>;
}
