"""Capture authored domain state and reuse the existing source identifiers."""

from hashlib import sha256
import json
from uuid import UUID

from fastapi import HTTPException
from pydantic_core import to_jsonable_python
from sqlalchemy import String, cast, func, select

from app.models.ai_execution import AIExecution
from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact, DesignArtifactAsset, DesignElement
from app.models.design_dna import DesignDNA
from app.models.presentation import Presentation, PresentationAsset, PresentationRevision
from app.models.production_procurement import ProductionBudget
from app.models.project_memory_item import ProjectMemoryItem
from app.models.spatial import SpatialLayer, SpatialObject, SpatialObjectAsset, SpatialObjectRelationship, SpatialScene, SpatialZone
from app.schemas.design_version import DesignSelection
from app.services import procurement_scope, production_budgets
from app.services.context import ContextService
from app.services.spatial import _get_scene, _missing, _row_state


_SCENE_ENTITIES = {"layers": SpatialLayer, "zones": SpatialZone, "objects": SpatialObject,
                   "relationships": SpatialObjectRelationship, "asset_links": SpatialObjectAsset}
_SECTIONS = {"scene_ids": "scenes", "artifact_ids": "artifacts", "budget_ids": "budgets", "bom_ids": "boms",
             "memory_item_ids": "memory", "design_dna_id": "design_dna", "presentation": "presentation"}
_SINGLES = {"design_dna_id", "presentation"}


def in_scope(row, project, event_id):
    return row is not None and row.project_id == project.id and row.event_id in (None, event_id)


def execution_in_scope(row, project, event_id):
    if (row is None or row.project_id != project.id or row.organization_id != project.organization_id
            or row.workspace_id not in (None, project.workspace_id) or row.status != "completed"):
        return False
    provenance = (row.metadata_json or {}).get("provenance") or {}
    scope = provenance.get("event_scope")
    return scope in (None, "00000000-0000-0000-0000-000000000000", str(event_id))


def asset_scope(asset, project, event_id):
    return (asset is not None and asset.organization_id == project.organization_id
            and asset.project_id in (None, project.id) and asset.workspace_id in (None, project.workspace_id)
            and asset.event_id in (None, event_id) and asset.status == "active" and asset.deleted_at is None)


def _subset(row, fields):
    return {key: getattr(row, key) for key in fields}


def _context(db, project, event):
    result = {"project": _subset(project, ("id", "version", "name", "description", "budget", "guest_count", "constraints")),
              "event": None, "venue": None, "venue_zones": {}}
    if event is not None:
        context = ContextService._event_context_item(db, event)
        result["event"] = _row_state(event)
        result["venue"] = _row_state(context["venue"])
        result["venue_zones"] = {str(row.id): _row_state(row) for row in context["venue_zones"]}
    return result


class Capture:
    def __init__(self, db, project, event, user_id):
        self.db, self.project, self.event, self.user_id = db, project, event, user_id
        self.event_id = event.id if event else None
        self.boms = procurement_scope.BOMSources(db, project, user_id, lock_rates=True)

    def _get(self, model, key, *, lock=True):
        return self.db.get(model, key, populate_existing=True, with_for_update={"read": True} if lock else None)

    def _scoped(self, model, key):
        row = self._get(model, key)
        if not in_scope(row, self.project, self.event_id):
            _missing()
        return row

    def scene(self, key):
        if not in_scope(self._get(SpatialScene, key, lock=False), self.project, self.event_id):
            _missing()
        scene = _get_scene(self.db, key, self.user_id, lock="read")
        if not in_scope(scene, self.project, self.event_id):
            _missing()
        return {"scene": _row_state(scene), **{
            name: {str(row.id): _row_state(row) for row in self.db.scalars(
                select(model).where(model.scene_id == key).order_by(model.id))}
            for name, model in _SCENE_ENTITIES.items()
        }}

    def artifact(self, key):
        # Read the artifact and its mutable elements in one statement. Phase 8 owns their write locks.
        asset_links = select(func.jsonb_object_agg(cast(DesignArtifactAsset.asset_id, String), DesignArtifactAsset.role)).where(
            DesignArtifactAsset.artifact_id == DesignArtifact.id,
        ).scalar_subquery()
        element_rows = select(func.jsonb_object_agg(cast(DesignElement.id, String), func.to_jsonb(DesignElement.__table__.table_valued()))).where(
            DesignElement.artifact_id == DesignArtifact.id, DesignElement.project_id == DesignArtifact.project_id,
            DesignElement.event_id.is_not_distinct_from(DesignArtifact.event_id),
        ).scalar_subquery()
        result = self.db.execute(select(DesignArtifact, asset_links, element_rows).where(DesignArtifact.id == key,
                                 DesignArtifact.project_id == self.project.id).execution_options(populate_existing=True)).one_or_none()
        if result is None or not in_scope(result[0], self.project, self.event_id):
            _missing()
        row, links, elements = result
        # Part 1 already uses the artifact and asset identifiers as provenance.
        content = _subset(row, ("id", "event_id", "module", "version", "status", "content", "based_on", "source"))
        run = self._get(AIExecution, row.ai_execution_id) if row.ai_execution_id else None
        content["execution_id"] = run.id if execution_in_scope(run, self.project, self.event_id) else None
        content["assets"] = links or {}
        content["elements"] = elements or {}
        return content

    def design_dna(self, key):
        row = self._get(DesignDNA, key)
        if row is None or row.project_id != self.project.id:
            _missing()
        return _row_state(row)

    def presentation(self, pin):
        # Holding a presentation row lock here would invert the domain scene/presentation lock order.
        row = self._get(Presentation, pin.id, lock=False)
        if not in_scope(row, self.project, self.event_id) or row.event_id != self.event_id:
            _missing()
        revision = self._get(PresentationRevision, (pin.id, pin.revision), lock=False)
        if revision is None:
            _missing()
        assets = list(self.db.scalars(select(PresentationAsset.asset_id).where(
            PresentationAsset.presentation_id == pin.id, PresentationAsset.revision == pin.revision,
        ).order_by(PresentationAsset.asset_id)))
        return {"id": pin.id, "revision": pin.revision, "title": revision.sheet["title"], "asset_ids": assets,
                "fingerprint": digest({"sheet": revision.sheet, "evidence": revision.evidence})}

    def budget(self, key):
        row = self._scoped(ProductionBudget, key)
        view = production_budgets._view(self.db, self.project, self.event if row.event_id else None, row, self.boms,
                                        latest=production_budgets._head(self.db, self.project, row.event_id).id == row.id)
        return {**{field: view[field] for field in ("id", "version", "currency", "summary", "approved_at", "latest", "stale_reasons")},
                "basis_bom_ids": [entry["id"] for entry in row.sources["boms"]]}

    def bom(self, key):
        result = self.boms.require(key)
        if not in_scope(result[1], self.project, self.event_id):
            _missing()
        view = result[2]
        return {**{field: view[field] for field in ("id", "record_id", "scene_id", "version", "scene_revision", "currency",
                                                    "costs", "approved_at", "latest", "stale")},
                "lines": {str(line["id"]): self._bom_line(line) for line in view["lines"]}}

    @staticmethod
    def _bom_line(line):
        source = line["inputs"].get("dependencies") or {}
        supports = {}
        for kind in ("quantity", "specification", "material", "component"):
            if source.get(kind):
                supports[kind] = {key: source[kind][key] for key in (
                    "id", "name", "version", "definition_id", "definition_version", "method", "scene_revision", "source", "status",
                    "stale", "superseded", "value", "unit", "technical",
                ) if key in source[kind]}
        return {**{key: line[key] for key in ("name", "line_key", "basis", "category", "quantity", "unit_rate", "total", "unit", "stale_reasons", "issues")},
                "dependencies": supports}

    def memory(self, key):
        row = self._scoped(ProjectMemoryItem, key)
        if row.status != "active":
            _missing()
        return _row_state(row)

    def assets(self, keys):
        assets = {}
        for key in sorted(keys):
            row = self._get(Asset, UUID(key))
            if not asset_scope(row, self.project, self.event_id):
                _missing()
            assets[key] = _subset(row, ("id", "display_name", "category", "mime_type", "checksum_sha256", "file_size"))
        return assets

    def build(self, selection, base=None, *, refresh_context=False):
        base = base or {}
        inputs = {**base.get("selection", {}), **selection.model_dump(mode="json", exclude_unset=True)}
        state = {**base, "selection": DesignSelection.model_validate(inputs).model_dump(mode="json")}
        if refresh_context or "context" not in state:
            state["context"] = _context(self.db, self.project, self.event)
        resolvers = {"scenes": self.scene, "artifacts": self.artifact, "budgets": self.budget, "boms": self.bom,
                     "memory": self.memory, "design_dna": self.design_dna, "presentation": self.presentation}
        for field, section in _SECTIONS.items():
            selected = getattr(selection, field)
            if field not in selection.model_fields_set and section in state:
                continue
            resolve = resolvers[section]
            if field in _SINGLES:
                state[section] = resolve(selected) if selected else None
            else:
                state[section] = {str(key): resolve(key) for key in sorted(selected)}
        state = to_jsonable_python(state)
        asset_ids = set(state["selection"]["asset_ids"])
        asset_ids.update(link["asset_id"] for item in state["scenes"].values() for link in item["asset_links"].values())
        asset_ids.update(key for item in state["artifacts"].values() for key in item["assets"])
        if state["presentation"]:
            asset_ids.update(state["presentation"]["asset_ids"])
        current_assets = self.assets(asset_ids)
        previous = base.get("assets", {})
        refreshed = {str(key) for key in selection.asset_ids} if "asset_ids" in selection.model_fields_set else set()
        state["assets"] = to_jsonable_python({key: value if key in refreshed else previous.get(key, value)
                                             for key, value in current_assets.items()})
        return state


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def live_basis(db, project, event, user_id, state):
    # Reuse the same capture path to detect changes to selected dependencies, including authored element edits.
    try:
        current = Capture(db, project, event, user_id).build(DesignSelection.model_validate(state["selection"]))
        # Preserve the Part 2 fork fingerprint when newer snapshots retain extra audit evidence.
        for budget in current["budgets"].values():
            budget.pop("basis_bom_ids", None)
        for bom in current["boms"].values():
            for line in bom["lines"].values():
                line.pop("dependencies", None)
                line.pop("category", None)
                line.pop("issues", None)
        return digest(current)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(409, detail={"code": "design_source_unavailable",
                                             "message": "A source in the mainline selection is unavailable. Save a current mainline first."}) from exc
        raise


def _entities(state):
    entries = {}
    context = state.get("context", {})
    for key in ("project", "event", "venue"):
        if context.get(key):
            entries[(key, context[key]["id"], None)] = context[key]
    for section in ("design_dna", "presentation"):
        if state.get(section):
            entries[(section, state[section]["id"], None)] = state[section]
    for section in ("artifacts", "budgets", "boms", "memory", "assets"):
        for key, value in state.get(section, {}).items():
            entries[(section, key, None)] = value
    for key, value in context.get("venue_zones", {}).items():
        entries[("venue_zone", key, None)] = value
    for scene_id, scene in state.get("scenes", {}).items():
        entries[("scene", scene_id, scene_id)] = scene["scene"]
        for section in _SCENE_ENTITIES:
            entries.update({(section, key, scene_id): value for key, value in scene[section].items()})
    return entries


def compare(before, after):
    older, newer = _entities(before), _entities(after)
    changes, affected = [], set()
    for kind, key, scene in sorted(older.keys() | newer.keys(), key=lambda pair: (pair[0], pair[1], pair[2] or "")):
        identity = (kind, key, scene)
        left, right = older.get(identity), newer.get(identity)
        if left == right:
            continue
        fields = sorted(field for field in (left or {}).keys() | (right or {}).keys()
                        if (left or {}).get(field) != (right or {}).get(field))
        changes.append({"kind": kind, "id": key, "scene_id": scene,
                        "change": "added" if left is None else "removed" if right is None else "updated", "fields": fields})
        affected.add(key)
        if scene:
            affected.add(scene)
    for state in (before, after):
        for key, bom in state.get("boms", {}).items():
            if bom["scene_id"] in affected:
                affected.add(key)
    return changes, sorted(affected)
