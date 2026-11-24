from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.project_memory_item import ProjectMemoryItem


class ProjectMemoryItemRepository:

    @staticmethod
    def get_by_id(db: Session, item_id: UUID, *, lock=False) -> Optional[ProjectMemoryItem]:
        statement = select(ProjectMemoryItem).where(ProjectMemoryItem.id == item_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return db.scalar(statement)

    @staticmethod
    def get_by_project(
        db: Session, project_id: UUID, *, include_archived: bool = False, category=None, event_id=None, offset=0, limit=None
    ):
        statement = select(ProjectMemoryItem).where(
            ProjectMemoryItem.project_id == project_id
        )
        if not include_archived:
            statement = statement.where(ProjectMemoryItem.status == "active")
        if category is not None:
            statement = statement.where(ProjectMemoryItem.category == category)
        if event_id is not None:
            statement = statement.where(or_(ProjectMemoryItem.event_id.is_(None), ProjectMemoryItem.event_id == event_id))
        statement = statement.order_by(
            ProjectMemoryItem.created_at, ProjectMemoryItem.id
        )
        return db.scalars(statement.offset(offset).limit(limit)).all()

    @staticmethod
    def get_context_for_event(db: Session, project_id: UUID, event_id: UUID):
        return ProjectMemoryItemRepository.get_by_project(db, project_id, event_id=event_id)
