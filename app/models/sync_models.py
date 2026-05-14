from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SyncTarget = Literal["postgres", "neo4j", "all"]


class SyncRequest(BaseModel):
    targets: list[SyncTarget] = Field(default_factory=lambda: ["all"])
    create_schema: bool = True
    rebuild_neo4j: bool = False
    prune_missing: bool = False
    enable_embeddings: bool = True


class SyncResult(BaseModel):
    ok: bool
    job_id: str | None = None
    targets: list[str]
    counts: dict[str, int] = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)
    started_at: str
    completed_at: str
