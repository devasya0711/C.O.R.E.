"""Repository API and durable ingestion progress contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.repository_source import RepositorySource


class RepositoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    repo_url: str | None = Field(default=None, max_length=512)
    default_branch: str = Field(default="main", min_length=1, max_length=128)
    use_sample_monolith: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "RepositoryCreate":
        if not self.name.strip():
            raise ValueError("Repository name must not be blank")
        if self.use_sample_monolith:
            if self.repo_url not in {None, "local://sample_monolith"}:
                raise ValueError("Sample ingestion cannot specify another source")
            self.repo_url = "local://sample_monolith"
        if self.repo_url is None:
            raise ValueError("Repository URL is required")
        self.repo_url = RepositorySource.validate_repo_url(self.repo_url)
        self.default_branch = RepositorySource.validate_branch(self.default_branch)
        return self


class RepositoryAccepted(BaseModel):
    repo_id: UUID
    status: Literal["INGESTING"] = "INGESTING"


class IngestionProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    stage: str
    progress: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class RepositorySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    repo_url: str
    default_branch: str
    last_commit_sha: str | None
    status: str
    node_count: int
    edge_count: int
    created_at: datetime
    ingestion: IngestionProgress | None = None


class IngestionMessage(BaseModel):
    """Only identifiers cross the durable queue, never PATs or source code."""

    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    repo_id: UUID
    job_id: UUID


class IngestionEvent(BaseModel):
    job_id: UUID
    repo_id: UUID
    stage: str
    progress: int
    status: str
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
