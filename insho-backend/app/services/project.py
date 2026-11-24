from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.workspace import Workspace
from app.repositories.project import ProjectRepository
from app.models.spatial import SpatialScene
from app.services import procurement_scope
from app.services.spatial_policy import delete_if_scenes_unlocked
from app.services.spatial_validation_config import stored_config
from app.security import require_membership, require_project_access
from app.schemas.project import ProjectCreate, ProjectUpdate


class ProjectService:

    @staticmethod
    def create(db: Session, data: ProjectCreate, user_id: UUID):
        require_membership(db, user_id, data.organization_id, write=True)

        workspace = db.get(Workspace, data.workspace_id)

        if not workspace or workspace.organization_id != data.organization_id:
            raise HTTPException(status_code=404, detail="Resource not found")

        stored_config(data.constraints, "constraints")
        return ProjectRepository.create(db, data.model_dump())

    @staticmethod
    def get_by_id(db: Session, project_id: UUID, user_id: UUID):
        require_project_access(db, user_id, project_id)
        project = ProjectRepository.get_by_id(db, project_id)

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        return project

    @staticmethod
    def get_all(db: Session, organization_id: UUID, user_id: UUID, *, offset: int = 0,
                limit: int = 50, status: Optional[str] = None,
                workspace_id: Optional[UUID] = None):
        require_membership(db, user_id, organization_id)
        if workspace_id is not None:
            workspace = db.get(Workspace, workspace_id)
            if not workspace or workspace.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")
        return ProjectRepository.get_all(
            db, organization_id, offset=offset, limit=limit, status=status,
            workspace_id=workspace_id,
        )

    @staticmethod
    def update(db: Session, project_id: UUID, data: ProjectUpdate, user_id: UUID):
        project = require_project_access(db, user_id, project_id, write=True)
        payload = data.model_dump(exclude_unset=True)
        if "constraints" in payload:
            stored_config(payload["constraints"], "constraints")

        for field in ("name", "status"):
            if field in payload and payload[field] is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Project {field} cannot be null"
                )

        allowed_transitions = {
            "draft": {"active", "completed", "archived"},
            "active": {"completed", "archived"},
            "completed": {"archived"},
            "archived": {"archived"},
        }
        new_status = payload.get("status", project.status)
        if new_status != project.status and new_status not in allowed_transitions.get(project.status, set()):
            raise HTTPException(status_code=409, detail="Unsupported project status transition")

        return ProjectRepository.update(db, project, payload)

    @staticmethod
    def delete(db: Session, project_id: UUID, user_id: UUID):
        project = procurement_scope.project_scope(db, project_id, user_id, write=True)

        def delete():
            procurement_scope.protect_container_history(db, project)
            ProjectRepository.delete(db, project)

        delete_if_scenes_unlocked(db, SpatialScene.project_id == project.id, delete)
