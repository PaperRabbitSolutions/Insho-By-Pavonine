"""Shared graph state and locking rules for canonical spatial mutations."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.spatial import SpatialLayer, SpatialObject, SpatialScene, SpatialZone


ORDER_STEP = 1024


@dataclass(frozen=True)
class EffectiveState:
    visible: bool = True
    locked: bool = False


def local_state(row):
    if isinstance(row, SpatialLayer):
        return EffectiveState(row.is_visible, row.is_locked)
    return EffectiveState(row.status == "active", row.is_locked)


def state_sources(row):
    if isinstance(row, SpatialObject):
        return ((SpatialObject, row.parent_object_id), (SpatialZone, row.zone_id), (SpatialLayer, row.layer_id))
    if isinstance(row, SpatialZone):
        return ((SpatialZone, row.parent_zone_id),)
    return ()


class StateResolver:
    def __init__(self, lookup):
        self.lookup = lookup
        self.cache = {}

    def resolve(self, row):
        root = (type(row), row.id)
        pending, visiting = [(root, row, False)], set()
        while pending:
            key, node, ready = pending.pop()
            if key in self.cache:
                continue
            if ready:
                self.cache[key] = local_state(node) if node is not None else EffectiveState()
                if node is not None:
                    local = self.cache[key]
                    states = [self.cache[(kind, ref)] for kind, ref in state_sources(node) if ref is not None]
                    self.cache[key] = EffectiveState(
                        local.visible and all(state.visible for state in states),
                        local.locked or any(state.locked for state in states),
                    )
                visiting.remove(key)
            else:
                if key in visiting:
                    raise HTTPException(409, detail="The scene graph contains a cycle.")
                visiting.add(key)
                pending.append((key, node, True))
                if node is not None:
                    for kind, ref in state_sources(node):
                        if ref is not None and (kind, ref) not in self.cache:
                            pending.append(((kind, ref), self.lookup(kind, ref), False))
        return self.cache[root]


def scene_row(db, model, scene_id, row_id):
    if row_id is None:
        return None
    row = db.get(model, row_id)
    if row is None or row.scene_id != scene_id:
        raise HTTPException(404, detail="Resource not found")
    return row


def locked():
    raise HTTPException(409, detail="The operation affects a locked scene graph entity.")


def require_unlocked(db, *rows):
    resolver = StateResolver(db.get)
    for row in rows:
        if row is not None and resolver.resolve(row).locked:
            locked()


def _branch(model, parent, roots, scene_id):
    branch = roots.cte(recursive=True)
    return branch.union(select(model.id).where(model.scene_id == scene_id, parent == branch.c.id))


def require_branch_unlocked(db, row):
    """Protect descendants when a transform or structural move can affect them."""
    if isinstance(row, SpatialZone):
        zones = _branch(SpatialZone, SpatialZone.parent_zone_id, select(SpatialZone.id).where(SpatialZone.id == row.id), row.scene_id)
        zone_ids = select(zones.c.id)
        roots = select(SpatialObject.id).where(SpatialObject.scene_id == row.scene_id, SpatialObject.zone_id.in_(zone_ids))
        zone_rows = db.scalars(select(SpatialZone).where(SpatialZone.id.in_(zone_ids))).all()
    else:
        roots = select(SpatialObject.id).where(SpatialObject.id == row.id)
        zone_rows = []
    objects = _branch(SpatialObject, SpatialObject.parent_object_id, roots, row.scene_id)
    require_unlocked(db, *zone_rows, *db.scalars(select(SpatialObject).where(SpatialObject.id.in_(select(objects.c.id)))).all())


def sibling_queries(scene_id, *, object_parent=None, zone_parent=None):
    objects = select(SpatialObject).where(SpatialObject.scene_id == scene_id, SpatialObject.parent_object_id == object_parent)
    if object_parent is not None:
        return (objects,)
    zones = select(SpatialZone).where(SpatialZone.scene_id == scene_id, SpatialZone.parent_zone_id == zone_parent)
    return (zones, objects.where(SpatialObject.zone_id == zone_parent))


def order_key(row):
    return (row.order_index, 0 if isinstance(row, SpatialZone) else 1, row.created_at, row.id.int)


def append_order(db, scene_id: UUID, *, object_parent=None, zone_parent=None, layer=False):
    if layer:
        return (db.scalar(select(func.max(SpatialLayer.order_index)).where(SpatialLayer.scene_id == scene_id)) or 0) + ORDER_STEP
    ranks = []
    for query in sibling_queries(scene_id, object_parent=object_parent, zone_parent=zone_parent):
        model = query.column_descriptions[0]["entity"]
        ranks.append(db.scalar(query.with_only_columns(func.max(model.order_index))) or 0)
    return max(ranks) + ORDER_STEP


def append_layer_member_order(db, scene_id, layer_id):
    if layer_id is None:
        return None
    rank = db.scalar(select(func.max(SpatialObject.layer_order_index)).where(
        SpatialObject.scene_id == scene_id, SpatialObject.layer_id == layer_id,
    ))
    return (rank or 0) + ORDER_STEP


def default_layer(db, scene_id):
    return db.scalars(select(SpatialLayer).where(
        SpatialLayer.scene_id == scene_id, SpatialLayer.is_default.is_(True),
    )).one_or_none()


def delete_if_scenes_unlocked(db, scene_filter, delete):
    """Serialize container deletion with graph writers and protect locked scenes."""
    scene_ids = db.scalars(select(SpatialScene.id).where(scene_filter).order_by(SpatialScene.id).with_for_update()).all()
    if scene_ids:
        for model in (SpatialLayer, SpatialZone, SpatialObject):
            if db.scalar(select(model.id).where(model.scene_id.in_(scene_ids), model.is_locked.is_(True)).limit(1)):
                locked()
    try:
        delete()
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "ck_spatial_scene_delete_locked":
            locked()
        if constraint in {"fk_presentations_project", "fk_presentations_event", "fk_presentations_event_project"}:
            raise HTTPException(409, detail={"code": "presentation_history_retained",
                                             "message": "This project or event retains presentation revisions."}) from exc
        if constraint in {"fk_design_versions_project", "fk_design_versions_event", "fk_design_branches_project",
                          "fk_design_branches_event", "fk_design_snapshots_project"}:
            raise HTTPException(409, detail={"code": "design_history_retained",
                                             "message": "This project or event retains design versions."}) from exc
        if constraint in {"fk_learning_actual_event", "fk_learning_report_event", "learning_reports_project_id_fkey"}:
            raise HTTPException(409, detail={"code": "learning_history_retained",
                                             "message": "This project or event retains actuals or learning reports."}) from exc
        raise
