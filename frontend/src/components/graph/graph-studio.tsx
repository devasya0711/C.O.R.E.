"use client";

import "@xyflow/react/dist/style.css";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
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
import { getRepositoryGraph } from "@/lib/api/repositories";
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
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<StudioEdge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>("ALL");
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("ALL");
  const [search, setSearch] = useState("");
  const [showCommunities, setShowCommunities] = useState(true);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

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

  const visibleNodes = useMemo(
    () =>
      filteredNodes.map((node) => ({
        ...node,
        data: { ...node.data, showCommunity: showCommunities },
        style: {
          opacity: selectedNodeId && selectedNodeId !== node.id ? 0.25 : 1,
          transition: "opacity 150ms ease",
        },
      })),
    [filteredNodes, selectedNodeId, showCommunities],
  );

  const visibleEdges = useMemo(
    () =>
      filteredEdges.map((edge) => {
        const relatedToSelection =
          selectedNodeId !== null &&
          (edge.source === selectedNodeId || edge.target === selectedNodeId);
        return {
          ...edge,
          animated: relatedToSelection && !prefersReducedMotion,
          style: {
            stroke: relatedToSelection ? "#EF4444" : "#4B5563",
            strokeWidth: relatedToSelection ? 2 : 1,
          },
          labelStyle: { fill: "#9CA3AF", fontSize: 9, fontFamily: "monospace" },
        };
      }),
    [filteredEdges, prefersReducedMotion, selectedNodeId],
  );

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;

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
      <Inspector node={selectedNode} />
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

function Inspector({ node }: { node: StudioNode | null }) {
  return (
    <aside aria-label="Node inspector" className={`absolute bottom-0 left-0 right-0 z-10 max-h-[45vh] overflow-y-auto border-t border-hairline bg-panel/95 p-5 shadow-2xl shadow-black/30 backdrop-blur lg:bottom-4 lg:left-auto lg:right-4 lg:top-4 lg:w-[380px] lg:max-h-none lg:rounded-lg lg:border ${node ? "block" : "hidden lg:block"}`}>
      <InspectorContent node={node} />
    </aside>
  );
}

function InspectorContent({ node }: { node: StudioNode | null }) {
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
