from pydantic import BaseModel, ConfigDict, Field


class LessonItem(BaseModel):
    lesson_num: str | int | None = None
    lesson_name: str | None = None
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


class LessonListPayload(BaseModel):
    lessons: list[LessonItem] = Field(default_factory=list)
