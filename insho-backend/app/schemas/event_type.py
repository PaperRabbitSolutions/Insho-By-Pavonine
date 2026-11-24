from datetime import datetime
from typing import Optional
from uuid import UUID


from pydantic import BaseModel, ConfigDict, Field

#Used when creating an eventtype
class EventTypeCreate(BaseModel):
    name: str = Field(min_length=2,max_length=150)
    description:Optional[str] = None

#Used when updating  an eventtype
class EventTypeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description:Optional[str] = None

#Used when returning an eventtype
class EventTypeResponse(BaseModel):
    id: UUID
    name: str
    description:Optional[str]
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True)