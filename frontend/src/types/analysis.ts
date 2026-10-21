import type { GraphRelationship } from "@/types/graph";

export interface SeamSummary {
  id: string;
  community_id: number;
  suggested_name: string;
  cohesion_score: number;
  shared_db_tables: string[];
  node_ids: string[];
}

export interface SeamsResponse {
  seams: SeamSummary[];
}

export interface ImpactRequest {
  target_qualified_name: string;
  max_hops: number;
  proposed_change_summary: string;
}

export interface ImpactEdge {
  source: string;
  target: string;
  hops: number;
}

export interface ImpactResponse {
  target: string;
  risk_score: number;
  affected_nodes: string[];
  affected_edges: ImpactEdge[];
  affected_db_tables: string[];
  impact_summary: string;
}

export type AnalysisRelationship = GraphRelationship;
