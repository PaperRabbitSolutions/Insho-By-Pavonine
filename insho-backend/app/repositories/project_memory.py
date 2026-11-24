from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_memory import ProjectMemory


class ProjectMemoryRepository:

    @staticmethod
    def create(db: Session, data: dict) -> ProjectMemory:
        memory = ProjectMemory(**data)
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory

    @staticmethod
    def get_by_id(db: Session, memory_id: UUID):
        statement = select(ProjectMemory).where(
            ProjectMemory.id == memory_id
        )
        return db.scalars(statement).first()

    @staticmethod
    def get_by_project(db: Session, project_id: UUID):
        statement = select(ProjectMemory).where(
            ProjectMemory.project_id == project_id
        )
        return db.scalars(statement).first()

    @staticmethod
    def update(db: Session, memory: ProjectMemory, data: dict):
        for field, value in data.items():
            setattr(memory, field, value)

        db.commit()
        db.refresh(memory)
        return memory

    @staticmethod
    def delete(db: Session, memory: ProjectMemory):
        db.delete(memory)
        db.commit()
