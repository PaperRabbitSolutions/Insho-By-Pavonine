from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.project_memory import ProjectMemoryRepository
from app.schemas.project_memory import (
    ProjectMemoryCreate,
    ProjectMemoryUpdate,
)
from app.security import require_project_access


class ProjectMemoryService:

    @staticmethod
    def create(db: Session, data: ProjectMemoryCreate, user_id: UUID):
        require_project_access(db, user_id, data.project_id, write=True)

        existing = ProjectMemoryRepository.get_by_project(
            db, data.project_id
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Project memory already exists"
            )

        return ProjectMemoryRepository.create(
            db, data.model_dump()
        )

    @staticmethod
    def get_by_id(db: Session, memory_id: UUID, user_id: UUID):
        memory = ProjectMemoryRepository.get_by_id(
            db, memory_id
        )

        if not memory:
            raise HTTPException(
                status_code=404,
                detail="Project memory not found"
            )
        require_project_access(db, user_id, memory.project_id)

        return memory

    @staticmethod
    def get_by_project(db: Session, project_id: UUID, user_id: UUID):
        require_project_access(db, user_id, project_id)

        memory = ProjectMemoryRepository.get_by_project(
            db, project_id
        )

        if not memory:
            raise HTTPException(
                status_code=404,
                detail="Project memory not found"
            )

        return memory

    @staticmethod
    def update(db: Session, memory_id: UUID, data: ProjectMemoryUpdate, user_id: UUID):
        memory = ProjectMemoryService.get_by_id(
            db, memory_id, user_id
        )
        require_project_access(db, user_id, memory.project_id, write=True)

        payload = data.model_dump(exclude_unset=True)

        return ProjectMemoryRepository.update(
            db, memory, payload
        )

    @staticmethod
    def delete(db: Session, memory_id: UUID, user_id: UUID):
        memory = ProjectMemoryService.get_by_id(
            db, memory_id, user_id
        )
        require_project_access(db, user_id, memory.project_id, write=True)

        ProjectMemoryRepository.delete(db, memory)
