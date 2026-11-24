"""Build isolated audit inputs from the exact immutable design version."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import DateTime, Numeric, Uuid, inspect, select

from app.models.design_version import DesignVersionReview
from app.models.presentation import Presentation, PresentationCheckpoint, PresentationRevision
from app.models.spatial import SpatialLayer, SpatialObject, SpatialObjectAsset, SpatialObjectRelationship, SpatialScene, SpatialZone
from app.schemas.design_audit import AuditWarning, SourceReference
from app.schemas.spatial_validation import ValidationReportRequest
from app.services import design_snapshot, design_version, spatial_validation, spatial_validation_rules
from app.services.spatial_graph import GraphRead


MAX_SCENE_NODES = 250
SPATIAL_FINDING_LIMIT = 100


@dataclass(frozen=True)
class AuditContext:
    version_id: UUID
    state: dict
    presentation: dict
    checkpoints: list[dict]
    unavailable_assets: list[str]
    warnings: list[AuditWarning]

    def fingerprint(self):
        return design_snapshot.digest({"version_id": str(self.version_id), "state": self.state,
                                       "presentation_available": bool(self.presentation),
                                       "checkpoints": self.checkpoints, "unavailable_assets": self.unavailable_assets})

    def pin_sources(self, kind):
        return [source for section in self.presentation.get("evidence", {}).values()
                for source in section["sources"] if source["kind"] == kind]

    def ref(self):
        return SourceReference(kind="design_version", id=self.version_id)


def load(db, project, version):
    state = design_version._snapshot(db, version).state
    warnings = []
    frozen_sheet, checkpoints = {}, []
    pin = state.get("presentation")
    if pin:
        sheet_id = UUID(pin["id"])
        row = db.get(Presentation, sheet_id)
        revision = db.get(PresentationRevision, (sheet_id, pin["revision"]))
        if (design_snapshot.in_scope(row, project, version.event_id) and row.event_id == version.event_id and revision
                and pin["fingerprint"] == design_snapshot.digest({"sheet": revision.sheet, "evidence": revision.evidence})):
            frozen_sheet = {"id": pin["id"], "revision": pin["revision"], "sheet": revision.sheet, "evidence": revision.evidence}
            review = db.get(DesignVersionReview, version.id)
            if review is not None and (review.presentation_id, review.presentation_revision) == (sheet_id, pin["revision"]):
                for checkpoint in db.scalars(select(PresentationCheckpoint).where(
                    PresentationCheckpoint.presentation_id == sheet_id, PresentationCheckpoint.revision == pin["revision"],
                ).order_by(PresentationCheckpoint.id)):
                    checkpoints.append({"id": str(checkpoint.id), "revision": checkpoint.revision, "status": checkpoint.status,
                                        "decided_at": checkpoint.decided_at.isoformat() if checkpoint.decided_at else None})
        else:
            warnings.append(AuditWarning(code="presentation_evidence_unavailable", message="The pinned presentation evidence could not be verified.",
                                         source=SourceReference(kind="presentation_revision", id=sheet_id, revision=pin["revision"])))
    unavailable = design_version._unavailable_assets(db, project, version.event_id, state)
    if unavailable:
        warnings.append(AuditWarning(code="asset_evidence_unavailable", message="Some captured asset references are currently unavailable in this scope."))
    return AuditContext(version.id, state, frozen_sheet, checkpoints, unavailable, warnings)


def _row(model, data):
    values = {}
    for field in inspect(model).column_attrs:
        if field.key not in data:
            continue
        value = data[field.key]
        column_type = field.columns[0].type
        if isinstance(value, str):
            if isinstance(column_type, Uuid):
                value = UUID(value)
            elif isinstance(column_type, Numeric):
                value = Decimal(value)
            elif isinstance(column_type, DateTime):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        values[field.key] = value
    return model(**values)


def _clearance_has_coverage(ctx, rule):
    subjects = spatial_validation_rules._matching(ctx, rule.subject)
    if not subjects or any(("object", key) not in ctx.projected for key in subjects):
        return False
    if hasattr(rule.against, "zone_footprint_id"):
        return ("zone", rule.against.zone_footprint_id) in ctx.projected
    against = spatial_validation_rules._matching(ctx, rule.against)
    return bool(against) and len(subjects | against) > 1 and all(("object", key) in ctx.projected for key in against)


def spatial_reports(audit):
    reports, warnings = [], []
    context_project = audit.state.get("context", {}).get("project", {})
    project = SimpleNamespace(constraints=context_project.get("constraints"))
    for key, capture in audit.state.get("scenes", {}).items():
        scene_row = capture["scene"]
        source = SourceReference(kind="scene", id=key, revision=scene_row["current_revision"])
        if len(capture["objects"]) + len(capture["zones"]) > MAX_SCENE_NODES:
            warnings.append(AuditWarning(code="spatial_review_size_limit", source=source,
                                         message="This captured scene exceeds the audit's geometry review limit."))
            continue
        scene = _row(SpatialScene, scene_row)
        graph = GraphRead.from_rows(scene, layers=[_row(SpatialLayer, row) for row in capture["layers"].values()],
                                    zones=[_row(SpatialZone, row) for row in capture["zones"].values()],
                                    objects=[_row(SpatialObject, row) for row in capture["objects"].values()],
                                    assets=[_row(SpatialObjectAsset, row) for row in capture["asset_links"].values()])
        request = ValidationReportRequest(limit=SPATIAL_FINDING_LIMIT)
        try:
            ctx = spatial_validation.SceneValidation(None, scene, request, project=project, graph=graph, relationships=[
                _row(SpatialObjectRelationship, row) for row in capture["relationships"].values()
            ])
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            warnings.append(AuditWarning(code="captured_spatial_policy_invalid", message="The frozen spatial policy is invalid.", source=source))
            continue
        report = spatial_validation.evaluate_context(ctx, request, include_live_catalog=False)
        report["measured_objects"] = {uid: ctx.bounds[("object", uid)] for uid, row in ctx.objects.items()
                                      if ("object", uid) in ctx.bounds and ctx.nodes[("object", uid)]["effective_visible"]}
        report["clearance_scope"] = {rule.id: _clearance_has_coverage(ctx, rule) for rule in ctx.config.clearances or ()}
        report["ref"] = source
        report["authored_dimensions_m"] = {name: scene_row[name] for name in ("width_m", "depth_m", "height_m")}
        report["truncated"] = report["filtered_total"] > len(report["findings"])
        if report["truncated"]:
            warnings.append(AuditWarning(code="spatial_findings_truncated", source=source,
                                         message="The stored evidence lists the first 100 Phase 9 findings for this scene."))
        reports.append(report)
    return reports, warnings
