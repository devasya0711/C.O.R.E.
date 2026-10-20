export type GraphNodeLabel = "File" | "Class" | "Function" | "APIEndpoint" | "DBTable";

export type GraphRelationship =
  | "CONTAINS"
  | "CALLS"
  | "INHERITS"
  | "READS_FROM"
  | "WRITES_TO"
  | "ROUTES_TO";

export interface GraphNodeData {
  [key: string]: unknown;
  label: GraphNodeLabel;
  qualified_name: string;
  name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  docstring: string;
  source_code: string;
  community_id: number | null;
  embedding: number[] | null;
  shared_db_tables: string[];
  incoming_count: number;
  outgoing_count: number;
}

export interface GraphPosition {
  x: number;
  y: number;
}

export interface GraphNode {
  id: string;
  type: "codeNode";
  position: GraphPosition;
  data: GraphNodeData;
}

export interface GraphEdgeData {
  [key: string]: unknown;
  source: string;
  target: string;
  relationship: GraphRelationship;
  line: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: GraphRelationship;
  data: GraphEdgeData;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
