from pydantic import BaseModel, ConfigDict, Field


class TopicItem(BaseModel):
    topic_num: str | int | None = None
    topic_name: str | None = None
    start: int
    end: int
    raw_heading: str | None = None
    raw_title: str | None = None
    name: str | None = None
    heading: str | None = None
    title: str | None = None

    model_config = ConfigDict(extra="allow")


class TopicListPayload(BaseModel):
    topics: list[TopicItem] = Field(default_factory=list)
