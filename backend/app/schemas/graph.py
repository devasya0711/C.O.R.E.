"""Repository-independent AST graph and React Flow transport models."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NodeLabel = Literal["File", "Class", "Function", "APIEndpoint", "DBTable"]
Relationship = Literal[
    "CONTAINS", "CALLS", "INHERITS", "READS_FROM", "WRITES_TO", "ROUTES_TO"
]


class CodeNode(BaseModel):
    """Source metadata before the storage layer adds its tenant namespace."""

    model_config = ConfigDict(extra="forbid")
    label: NodeLabel
    qualified_name: str = Field(min_length=1)
    name: str = Field(min_length=1)
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    docstring: str = ""
    source_code: str = ""
    community_id: int | None = None
    embedding: list[float] | None = None

    @model_validator(mode="after")
    def valid_extent(self) -> CodeNode:
        if self.end_line < self.start_line:
            raise ValueError("Source extent must not be reversed")
        return self

    @field_validator("embedding")
    @classmethod
    def valid_embedding(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and (
            len(value) != 1536 or not all(math.isfinite(item) for item in value)
        ):
            raise ValueError("Function embeddings must have 1536 finite dimensions")
        return value


class CodeEdge(BaseModel):
    """Directed relationship between qualified source symbols."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str
    target: str
    relationship: Relationship
    line: int | None = Field(default=None, ge=1)


class CodeGraph(BaseModel):
    """Validated deterministic analyzer output; no repository code is executed."""

    model_config = ConfigDict(extra="forbid")
    nodes: list[CodeNode]
    edges: list[CodeEdge]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_symbols(self) -> CodeGraph:
        labels = {node.qualified_name: node.label for node in self.nodes}
        names = set(labels)
        if len(names) != len(self.nodes):
            raise ValueError("Graph symbols must be unique")
        if any(
            edge.source not in names or edge.target not in names for edge in self.edges
        ):
            raise ValueError("Graph edges must reference existing nodes")
        allowed = {
            ("CONTAINS", "File", "Class"),
            ("CONTAINS", "File", "Function"),
            ("CONTAINS", "Class", "Function"),
            ("CALLS", "Function", "Function"),
            ("INHERITS", "Class", "Class"),
            ("READS_FROM", "Function", "DBTable"),
            ("WRITES_TO", "Function", "DBTable"),
            ("ROUTES_TO", "APIEndpoint", "Function"),
        }
        if any(
            (edge.relationship, labels[edge.source], labels[edge.target]) not in allowed
            for edge in self.edges
        ):
            raise ValueError("Graph relationships must follow the database schema")
        return self


class NodeData(CodeNode):
    """Graph-studio metadata, including the shared-table results from Agent 2."""

    shared_db_tables: list[str] = Field(default_factory=list)
    incoming_count: int = 0
    outgoing_count: int = 0


class Position(BaseModel):
    x: float
    y: float


class FlowNode(BaseModel):
    id: str
    type: Literal["codeNode"] = "codeNode"
    position: Position
    data: NodeData


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    label: Relationship
    data: CodeEdge


class GraphResponse(BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
