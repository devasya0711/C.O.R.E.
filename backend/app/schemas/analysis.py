"""Validated community-analysis and blast-radius contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Community(BaseModel):
    """Internal deterministic metrics; public seams add persistent IDs/names."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    community_id: int = Field(ge=0)
    node_ids: list[str]
    cohesion_score: float = Field(ge=0, le=1)
    coupling_score: float = Field(ge=0, le=1)
    shared_db_tables: list[str]


class BoundaryResult(BaseModel):
    communities: list[Community]
    assignments: dict[str, int]
    method: Literal["gds", "networkx"]


class SeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    community_id: int
    suggested_name: str
    cohesion_score: float
    shared_db_tables: list[str]
    node_ids: list[str]


class SeamsResponse(BaseModel):
    seams: list[SeamResponse]


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_qualified_name: str = Field(min_length=1, max_length=1024)
    max_hops: Annotated[StrictInt, Field(ge=1, le=5)] = 3
    proposed_change_summary: str = Field(default="", max_length=4000)


class ImpactEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    hops: int = Field(ge=0, le=5)


class ImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    risk_score: int = Field(ge=0, le=100)
    affected_nodes: list[str]
    affected_edges: list[ImpactEdge]
    affected_db_tables: list[str]
    impact_summary: str
