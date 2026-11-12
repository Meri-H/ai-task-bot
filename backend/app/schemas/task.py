from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from app.models import TaskSource, TaskStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str | None = None
    first_name: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_name(self) -> str:
        return self.first_name or (f"@{self.username}" if self.username else f"user {self.id}")


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    status: TaskStatus
    source: TaskSource
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    user: UserRead | None = None


class TaskCreate(BaseModel):
    title: Title
    description: str | None = Field(default=None, max_length=4000)


class TaskUpdate(BaseModel):
    status: TaskStatus


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int
