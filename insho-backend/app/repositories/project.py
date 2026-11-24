from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.project import Project


class ProjectRepository:

    @staticmethod
    def create(db: Session, data: dict) -> Project:
        project = Project(**data)
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def get_by_id(db: Session, project_id: UUID):
        statement = select(Project).where(Project.id == project_id)
        return db.scalars(statement).first()

    @staticmethod
    def get_all(db: Session, organization_id: UUID, *, offset: int = 0, limit: int = 50,
                status: Optional[str] = None, workspace_id: Optional[UUID] = None):
        statement = (
            select(Project)
            .where(Project.organization_id == organization_id)
            .order_by(Project.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(Project.status == status)
        if workspace_id is not None:
            statement = statement.where(Project.workspace_id == workspace_id)
        return db.scalars(statement).all()

    @staticmethod
    def update(db: Session, project: Project, data: dict):
        expected_version = data.pop("version", None)
        if expected_version is not None and project.version != expected_version:
            raise HTTPException(status_code=409, detail="Project was updated by another request")
        result = db.execute(
            update(Project)
            .where(Project.id == project.id, Project.version == project.version)
            .values(**data, version=Project.version + 1)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Project was updated by another request")
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def delete(db: Session, project: Project):
        db.delete(project)
        db.commit()
