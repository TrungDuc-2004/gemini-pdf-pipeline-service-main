from pydantic import BaseModel, ConfigDict, Field


class ChunkItem(BaseModel):
    chunk_id: str | None = None
    id: str | None = None
    lesson_num: str | int | None = None
    lesson_name: str | None = None
    lesson_stem: str | None = None
    chunk_num: str | int | None = None
    chunk_name: str | None = None
    heading: str | None = None
    title: str | None = None
    start: int
    end: int
    content_head: bool = False
    pdf_path: str | None = None
    metadata_path: str | None = None

    model_config = ConfigDict(extra="allow")


class ChunkListPayload(BaseModel):
    chunks: list[ChunkItem] = Field(default_factory=list)


class ChunkAddPayload(BaseModel):
    lesson_num: str | int | None = None
    lesson_stem: str | None = None
    chunk_num: str | int | None = None
    chunk_name: str | None = None
    title: str | None = None
    start: int
    end: int
    heading: str | None = None
    content_head: bool = False

    model_config = ConfigDict(extra="allow")


class ChunkRecutPayload(BaseModel):
    chunk_id: str | None = None
    lesson_stem: str
    chunk_num: str | int | None = None
    start: int
    end: int
    heading: str | None = None
    title: str | None = None
    content_head: bool = False

    model_config = ConfigDict(extra="allow")
