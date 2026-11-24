"""Project-authorized evidence projections for a Client Presentation Sheet."""

from uuid import UUID

from fastapi import HTTPException
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select

from app.models.ai_execution import AIExecution
from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact, DesignArtifactAsset
from app.models.design_dna import DesignDNA
from app.models.presentation import Presentation, PresentationRevision
from app.models.production import ProductionMaterial, ProductionQuantity, ProductionRecord
from app.models.production_bom import ProductionBOMLine
from app.models.production_procurement import ProductionBudget, ProductionProcurement
from app.models.project_memory_item import ProjectMemoryItem
from app.models.spatial import SpatialScene
from app.schemas.presentation import SectionContent
from app.services import production_bom, production_budgets, production_materials, production_quantities, production_workflow, spatial_sync
from app.services.design_intelligence import reference_in_scope


ALLOWED_SOURCES = {
    "concept_story": {"design_dna", "design_artifact", "project_memory_item"},
    "design_dna": {"design_dna"},
    "moodboard": {"design_artifact"},
    "hero_visual": {"design_artifact"},
    "spatial_plan": {"design_artifact", "spatial_scene"},
    "views_3d": {"design_artifact", "spatial_scene"},
    "journey": {"design_artifact", "project_memory_item"},
    "material_palette": {"design_dna", "design_artifact", "material_assignment"},
    "led_visual_preview": {"led_quantity", "spatial_scene"},
    "budget_summary": {"budget"},
    "production_confidence": {"production_record"},
    "alternatives": {"alternative"},
}
ARTIFACT_FIELDS = {
    "concept_story": {"style": ("creative_direction", "visual_identity"), "vision": ("concept_summary",)},
    "moodboard": {"mood": ("atmosphere", "keywords", "color_story", "colors", "visual_inspiration")},
    "hero_visual": {"vision": ("concept_summary",)},
    "spatial_plan": {"plan": ("summary", "zones", "adjacencies", "assumptions", "construction_accuracy")},
    "views_3d": {"vision": ("views", "limitations")},
    "journey": {"plan": ("guest_flow", "assumptions")},
    "material_palette": {"style": ("materials_and_finishes",), "mood": ("colors", "materials", "textures")},
}
ASSET_SECTIONS = {"moodboard", "hero_visual", "spatial_plan", "views_3d", "material_palette", "led_visual_preview"}
_VISUAL_ONLY = {"hero_visual", "views_3d", "led_visual_preview"}
_LED_TYPES = {"led", "led_wall", "led_screen", "led_surface"}


class UnavailableSource(Exception):
    pass


class IncompatibleSource(Exception):
    pass


def _fields(source, keys):
    return {key: source[key] for key in keys if key in source}


def _evidence(data, *, version=None, ready=True, stale=False):
    return to_jsonable_python({"data": data, "version": version, "ready": ready, "stale": stale})


class Sources:
    def __init__(self, db, project, presentation, user_id, *, lock=False):
        self.db = db
        self.project = project
        self.presentation = presentation
        self.event_id = presentation.event_id
        self.user_id = user_id
        self.lock = lock
        self.rows = {}
        self.scenes = {}
        self.artifact_assets = {}

    def _get(self, model, key, *, lock=None):
        cache_key = (model, key)
        if cache_key not in self.rows:
            read_lock = self.lock if lock is None else lock
            self.rows[cache_key] = self.db.get(model, key, populate_existing=True,
                                               with_for_update={"read": True} if read_lock else None)
        return self.rows[cache_key]

    def _scope(self, row):
        if row is None or row.project_id != self.project.id or row.event_id not in (None, self.event_id):
            raise UnavailableSource
        return row

    def asset(self, key, section):
        asset = self._get(Asset, key)
        if not reference_in_scope(asset, self.project, self.event_id):
            raise UnavailableSource
        visual = asset.mime_type.startswith(("image/", "video/"))
        if not visual and (section != "spatial_plan" or asset.category != "floor_plan"):
            raise IncompatibleSource("Choose a managed image or video. Spatial Plan also accepts a managed floor plan.")
        media = _fields(asset.media_metadata or {}, ("width", "height", "duration_seconds"))
        return {"name": asset.display_name, "category": asset.category, "mime_type": asset.mime_type,
                "checksum_sha256": asset.checksum_sha256, "media": media}

    def _asset_links(self, artifact):
        if artifact.id not in self.artifact_assets:
            self.artifact_assets[artifact.id] = self.db.scalars(select(DesignArtifactAsset).where(
                DesignArtifactAsset.artifact_id == artifact.id,
            ).order_by(DesignArtifactAsset.created_at, DesignArtifactAsset.asset_id)).all()
        return self.artifact_assets[artifact.id]

    def _artifacts(self, key, section):
        artifact = self._scope(self._get(DesignArtifact, key))
        fields = ARTIFACT_FIELDS.get(section, {}).get(artifact.module)
        if fields is None:
            raise IncompatibleSource("The design artifact module does not supply this presentation section.")
        content = _fields(artifact.content, fields)
        detail = {"module": artifact.module, "status": artifact.status, "source": artifact.source, "content": content}
        if artifact.ai_execution_id is not None:
            execution = self._get(AIExecution, artifact.ai_execution_id)
            if (execution is not None and execution.organization_id == self.project.organization_id
                    and execution.project_id == self.project.id and execution.workspace_id in (None, self.project.workspace_id)
                    and execution.status == "completed"):
                detail["generation"] = {"execution_id": execution.id, "provider": execution.provider, "model": execution.model}
        ready = any(content.values())
        if section == "journey":
            ready = bool(content.get("guest_flow"))
        elif section == "spatial_plan":
            ready = bool(content.get("zones"))
        elif section in _VISUAL_ONLY:
            ready = False
        links = []
        if section in ASSET_SECTIONS:
            for link in self._asset_links(artifact):
                if section in _VISUAL_ONLY and link.role != "generated":
                    continue
                # An old design artifact can have deleted, relocated, or nonvisual reference files.
                # Such links are omitted from new evidence and remain visible as unavailable in older revisions.
                try:
                    metadata = self.asset(link.asset_id, section)
                except (UnavailableSource, IncompatibleSource):
                    continue
                links.append({"id": str(link.asset_id), "role": link.role, "artifact_id": str(artifact.id), "metadata": metadata})
        return _evidence(detail, version=artifact.version, ready=ready, stale=artifact.status == "archived"), links

    def _scene(self, key):
        # First check the scene's scope. The existing spatial reader holds the scene lock during projection.
        row = self._scope(self._get(SpatialScene, key, lock=False))
        if row.deleted_at is not None:
            raise UnavailableSource
        return row

    def _scene_evidence(self, key, section):
        row = self._scene(key)
        if key not in self.scenes:
            try:
                pair = spatial_sync.synchronize_scene(self.db, key, self.user_id)
            except HTTPException as exc:
                # A soft deletion between the scope query and the spatial read is an unavailable source.
                if exc.status_code == 404:
                    raise UnavailableSource from exc
                raise
            self._scope(row)
            self.scenes[key] = {
                "plan_nodes": sum(node["renderable"] for node in pair["plan"]["nodes"]),
                "runtime_nodes": sum(node["renderable"] for node in pair["runtime"]["nodes"]),
                "contains_led": any((node.get("object_type") or "").casefold() in _LED_TYPES for node in pair["runtime"]["nodes"]),
                "projection_status": pair["validation"]["status"],
            }
        measures = self.scenes[key]
        key_for_section = "runtime_nodes" if section == "views_3d" else "plan_nodes"
        return _evidence({"scene_id": row.id, "name": row.name, **measures}, version=row.current_revision,
                         ready=bool(measures[key_for_section]), stale=row.status == "archived")

    def _production(self, key):
        row = self._get(ProductionRecord, key, lock=False)
        if row is None:
            raise UnavailableSource
        return row, self._scene(row.scene_id)

    def _production_evidence(self, key):
        record, scene = self._production(key)
        view = production_workflow.get(self.db, scene.id, record.id, self.user_id)
        latest_bom = production_bom._head(self.db, record)
        if latest_bom is not None:
            counts = self.db.execute(select(ProductionProcurement.status, func.count()).join(
                ProductionBOMLine, ProductionBOMLine.id == ProductionProcurement.bom_line_id,
            ).where(ProductionBOMLine.bom_id == latest_bom.id, ProductionProcurement.project_id == self.project.id)
              .group_by(ProductionProcurement.status)).all()
        else:
            counts = []
        detail = _fields(view, ("scene_id", "record_id", "status", "readiness_basis", "readiness_blockers", "readiness_stale"))
        detail["latest_bom"] = {"id": latest_bom.id, "version": latest_bom.version,
                                "approved": latest_bom.approved_at is not None} if latest_bom is not None else None
        detail.update(record_version=record.version, record_status=record.status, procurement_status_counts=dict(counts))
        return _evidence(detail, version=view["version"], ready=view["readiness_basis"] is not None,
                         stale=view["readiness_stale"])

    def _budget_evidence(self, key):
        row = self._scope(self._get(ProductionBudget, key))
        view = production_budgets.get_budget(self.db, self.project.id, key, self.user_id)
        # Commercial documents, supplier details and internal budget notes stay in their own APIs.
        detail = _fields(view, ("currency", "currency_precision", "summary", "approved_at", "latest", "stale_reasons"))
        return _evidence(detail, version=row.version, ready=bool(view["approved_at"]) or any(
            view["summary"][basis]["priced_subtotal"] is not None for basis in ("estimated", "quoted", "actual")
        ), stale=view["stale"] or not view["latest"])

    def _material_evidence(self, key):
        row = self._get(ProductionMaterial, key, lock=False)
        if row is None:
            raise UnavailableSource
        record, scene = self._production(row.record_id)
        view = production_materials.get_assignment(self.db, scene.id, record.id, key, self.user_id)
        effective = view["effective"] or {}
        detail = {"record_id": record.id, "scene_id": scene.id, "name": view["name"], "description": view["description"],
                  **_fields(effective, ("definition_id", "definition_version", "specification", "finish", "status"))}
        return _evidence(detail, version=view["version"], stale=effective.get("status", "active") != "active")

    def _led_evidence(self, key):
        row = self._get(ProductionQuantity, key, lock=False)
        if row is None:
            raise UnavailableSource
        record, scene = self._production(row.record_id)
        if row.method != "led_surface":
            raise IncompatibleSource("Select an LED surface calculation.")
        view = production_quantities.detail(self.db, scene.id, record.id, key, self.user_id)
        detail = _fields(view, ("scene_id", "scene_revision", "item_id", "source", "value", "unit", "outputs"))
        return _evidence(detail, version=row.version, ready=row.status == "calculated", stale=view["stale"] or not view["latest"])

    def _alternative_evidence(self, key, version):
        row = self._scope(self._get(Presentation, key, lock=False))
        if row.event_id != self.event_id or row.alternative_of_id != self.presentation.id:
            raise UnavailableSource
        revision = self._get(PresentationRevision, (row.id, version), lock=False)
        if revision is None:
            raise UnavailableSource
        design_sections = ("design_dna", "moodboard", "hero_visual", "spatial_plan", "views_3d", "material_palette")
        ready = any(self._alternative_has_evidence(revision, section) for section in design_sections)
        return _evidence(_fields(revision.sheet, ("title", "description", "purpose", "client_context")), version=version, ready=ready)

    def _alternative_has_evidence(self, revision, section):
        captured = revision.evidence.get(section)
        if captured is None:
            return False
        content = SectionContent.model_validate(revision.sheet["sections"][section])
        live_assets = {str(key) for key in content.asset_ids}
        for reference, pin in zip(content.sources, captured["sources"]):
            try:
                live, assets = self.resolve(reference, section)
            except (UnavailableSource, IncompatibleSource):
                continue
            if live == pin and not live["stale"]:
                if live["ready"]:
                    return True
                live_assets.update(asset["id"] for asset in assets)
        for asset in captured["assets"]:
            if asset["id"] not in live_assets:
                continue
            try:
                current = self.asset(UUID(asset["id"]), section)
            except (UnavailableSource, IncompatibleSource):
                continue
            if current == asset["metadata"]:
                return True
        return False

    def resolve(self, reference, section):
        kind, key = reference.kind, reference.id
        if kind not in ALLOWED_SOURCES[section]:
            raise IncompatibleSource("This source type does not supply the selected section.")
        links = []
        if kind == "design_artifact":
            evidence, links = self._artifacts(key, section)
        elif kind == "design_dna":
            row = self._get(DesignDNA, key)
            if row is None or row.project_id != self.project.id:
                raise UnavailableSource
            fields = ("palette", "materials") if section == "material_palette" else (
                "palette", "materials", "floral", "lighting", "architecture", "motifs", "cultural_inspiration", "mood", "formality",
            )
            detail = {field: getattr(row, field) for field in fields}
            evidence = _evidence(detail, version=row.version, ready=any(detail.values()))
        elif kind == "project_memory_item":
            row = self._scope(self._get(ProjectMemoryItem, key))
            # Only explicitly selected memory labels are surfaced. Internal notes and the preference payload stay in Memory.
            evidence = _evidence({"title": row.title, "kind": row.kind, "updated_at": row.updated_at},
                                 ready=False, stale=row.status != "active")
        elif kind == "spatial_scene":
            evidence = self._scene_evidence(key, section)
        elif kind == "production_record":
            evidence = self._production_evidence(key)
        elif kind == "material_assignment":
            evidence = self._material_evidence(key)
        elif kind == "budget":
            evidence = self._budget_evidence(key)
        elif kind == "led_quantity":
            evidence = self._led_evidence(key)
        else:
            evidence = self._alternative_evidence(key, reference.version)
        return {"id": str(key), "kind": kind, **evidence}, links
