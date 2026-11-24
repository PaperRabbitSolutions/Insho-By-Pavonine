from uuid import uuid4

from sqlalchemy import select

from app.models.design_version import DesignActivity, DesignVersion, DesignVersionReview


def record(db, project_id, event_id, actor_id, action, entity_type, entity_id, *, version_id=None, branch_id=None,
           reason=None, request_id=None, execution_id=None, **details):
    db.add(DesignActivity(project_id=project_id, event_id=event_id, actor_id=actor_id, action=action,
                          entity_type=entity_type, entity_id=entity_id, version_id=version_id, branch_id=branch_id,
                          reason=reason, details=details, request_id=request_id or uuid4(), execution_id=execution_id))


def presentation_review(db, presentation, revision, checkpoint, actor_id, request_id=None):
    version = db.scalar(select(DesignVersion).join(DesignVersionReview, DesignVersion.id == DesignVersionReview.version_id)
                        .where(DesignVersionReview.presentation_id == presentation.id,
                               DesignVersionReview.presentation_revision == revision))
    record(db, presentation.project_id, presentation.event_id, actor_id, "checkpoint_" + checkpoint.status,
           "presentation_checkpoint", checkpoint.id, version_id=version.id if version else None,
           branch_id=version.branch_id if version else None,
           reason=checkpoint.feedback if checkpoint.decided_at else checkpoint.request_comment, request_id=request_id,
           presentation_id=str(presentation.id), presentation_revision=revision,
           previous_status="pending" if checkpoint.decided_at else "draft", status=checkpoint.status)
