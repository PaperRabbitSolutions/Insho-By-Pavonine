from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.organization_member import OrganizationMember
from app.models.project import Project
from app.models.venue import Venue
from app.models.workspace import Workspace
from app.models.user import User

ROLES = {"owner", "admin", "designer", "member"}
ADMIN_ROLES = {"owner", "admin"}
EDITOR_ROLES = {"owner", "admin", "designer"}


def require_membership(db: Session, user_id: UUID, organization_id: UUID, write: bool = False):
    membership = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    ).first()
    if not membership or membership.role not in ROLES:
        raise HTTPException(status_code=404, detail="Resource not found")
    if write and membership.role not in EDITOR_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return membership


def require_project_access(
    db: Session, user_id: UUID, project_id: UUID, write: bool = False
) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Resource not found")
    workspace = db.get(Workspace, project.workspace_id)
    if not workspace or workspace.organization_id != project.organization_id:
        raise HTTPException(status_code=404, detail="Resource not found")
    require_membership(db, user_id, project.organization_id, write=write)
    return project


def require_event_access(
    db: Session, user_id: UUID, event_id: UUID, write: bool = False
) -> Event:
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Resource not found")
    if event.project_id is not None:
        project = require_project_access(db, user_id, event.project_id, write=write)
        if not project or project.organization_id != event.organization_id:
            raise HTTPException(status_code=404, detail="Resource not found")
    else:
        require_membership(db, user_id, event.organization_id, write=write)
    return event


def require_venue_access(
    db: Session, user_id: UUID, venue_id: UUID, write: bool = False
) -> Venue:
    venue = db.get(Venue, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="Resource not found")
    require_membership(db, user_id, venue.organization_id, write=write)
    return venue


def require_catalog_admin(db: Session, user_id: UUID) -> None:
    user = db.get(User, user_id)
    if not user or not user.is_system_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
