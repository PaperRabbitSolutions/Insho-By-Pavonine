"""Authorize, execute, and report spatial checks for one canonical scene revision."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from hashlib import sha256
import json
import math
from time import perf_counter
from typing import Callable
from uuid import UUID, uuid4, uuid5

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.models.project import Project
from app.models.spatial import SpatialObject, SpatialObjectRelationship
from app.schemas.spatial_validation import Category, RuleId, Severity, ValidationReportRequest
from app.services import spatial_validation_rules as checks
from app.services.spatial import _get_scene
from app.services.spatial_geometry import _object_item
from app.services.spatial_graph import GraphRead, _ref
from app.services.spatial_runtime import SceneCompilation
from app.services.spatial_sync import compile_pair
from app.services.spatial_validation_config import effective_config, fingerprint
from app.services.spatial_validation_library import object_library
from app.spatial.geometry3d import AABB3
from app.spatial.types import GeometryError
from app.spatial.validation import iter_aabb_pairs, scene_boundary


@dataclass(frozen=True)
class Rule:
    id: RuleId
    name: str
    category: Category
    default_severity: Severity
    description: str
    check: Callable


RULES = (
    Rule("geometry", "Geometry validity", "geometry", "error", "Check canonical dimensions and shapes.", checks.geometry),
    Rule("transforms", "Transform validity", "transform", "error", "Check local and composed world frames.", checks.transforms),
    Rule("hierarchy", "Scene graph integrity", "hierarchy", "error", "Check scene references, hierarchy and ordering.", checks.hierarchy),
    Rule("venue_boundary", "Venue boundary", "containment", "error", "Check measured geometry against the venue envelope.", checks.venue_boundary),
    Rule("zone_containment", "Zone containment", "containment", "warning", "Check assigned and nested XY footprints.", checks.zone_containment),
    Rule("zone_overlap", "Exclusive zones", "collision", "warning", "Check configured pairs of exclusive zones.", checks.zone_overlap),
    Rule("collision", "Object collision", "collision", "warning", "Check eligible object pairs for spatial intersection.", checks.collision),
    Rule("clearance", "Configured clearance", "clearance", "warning", "Check configured minimum surface separations.", checks.clearance),
    Rule("consistency", "Representation consistency", "consistency", "warning", "Check plan and runtime agreement and freshness.", checks.consistency),
    Rule("object_library", "Object library integrity", "library", "error", "Check pinned definitions and asset links.", object_library),
)
_FINDING_NAMESPACE = UUID("337d68b2-5dc1-4151-911b-15c7b013419e")
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class SceneValidation:
    def __init__(self, db, scene, request, *, boundary_zone_ids=(), project=None, graph=None, relationships=None):
        self.db, self.scene = db, scene
        self.project = project if project is not None else db.get(Project, scene.project_id)
        self.config = effective_config(self.project, scene, request.config)
        self.boundary_zone_ids = boundary_zone_ids
        self.graph = graph if graph is not None else GraphRead(db, scene)
        self.frozen_relationships = relationships
        compilation = SceneCompilation(self.graph)
        self.compiled = compile_pair(
            compilation, plan_revision=request.plan_revision, runtime_revision=request.runtime_revision,
        )
        self.projected = compilation.projected_footprints
        runtime = self.compiled["runtime"]
        self.nodes = {(node["type"], node["id"]): node for node in runtime["nodes"]}
        self.by_ref = {}
        for row in [*self.graph.nodes, *self.graph.layers]:
            ref = _ref(row)
            self.by_ref[ref["type"], ref["id"]] = row
        self.objects = {row.id: row for row in self.graph.nodes if isinstance(row, SpatialObject) and row.node_kind == "object"}
        self.venue = scene_boundary(**runtime["dimensions"]) if runtime["bounds"] is not None else None
        self.bounds = {}
        for ref, node in self.nodes.items():
            if node["geometry"] is not None:
                bounds = node["geometry"]["world_bounds"]
                self.bounds[ref] = AABB3(*bounds["minimum_m"], *bounds["maximum_m"])
        self.zones_by_object = {}
        for node in self.nodes.values():
            if node["type"] in {"object", "group"}:
                parent = node["transform_parent"]
                inherited = self.zones_by_object.get(parent["id"]) if parent is not None else None
                self.zones_by_object[node["id"]] = node["zone_id"] or inherited
        self.items = {}
        self.item_errors = {}
        self.findings = {}

    @cached_property
    def relationships(self):
        if self.frozen_relationships is not None:
            return self.frozen_relationships
        return self.db.scalars(select(SpatialObjectRelationship).where(
            SpatialObjectRelationship.scene_id == self.scene.id,
        ).order_by(SpatialObjectRelationship.id)).all()

    def are_related(self, first, second):
        for child, ancestor in ((first, second), (second, first)):
            visited = set()
            while child not in visited:
                visited.add(child)
                row = self.graph.by_key.get((SpatialObject, child))
                if row is None or row.parent_object_id is None:
                    break
                if row.parent_object_id == ancestor:
                    return True
                child = row.parent_object_id
        return False

    def pairs(self, ids, *, margin=0.0):
        bounds = [self.bounds["object", object_id] for object_id in ids]
        for left, right in iter_aabb_pairs(bounds, margin_m=margin):
            first, second = ids[left], ids[right]
            yield (first, second) if first < second else (second, first)

    def item(self, object_id):
        if object_id in self.item_errors:
            raise GeometryError(self.item_errors[object_id])
        if object_id not in self.items:
            ref = ("object", object_id)
            try:
                if ref not in self.projected:
                    raise GeometryError("The object has no supported exact projection for this measurement.")
                self.items[object_id] = _object_item(
                    self.objects[object_id], self.nodes[ref]["world_matrix"],
                    projected=self.projected[ref], world_bounds=self.bounds[ref],
                )
            except (GeometryError, OverflowError) as exc:
                self.item_errors[object_id] = str(exc)
                raise
        return self.items[object_id]

    def location(self, refs):
        bounds = [self.bounds[ref] for ref in refs if ref in self.bounds]
        if bounds:
            low = tuple(min(getattr(box, "min_" + axis) for box in bounds) for axis in "xyz")
            high = tuple(max(getattr(box, "max_" + axis) for box in bounds) for axis in "xyz")
        else:
            world = next((self.nodes[ref]["world_matrix"] for ref in refs
                          if ref in self.nodes and self.nodes[ref]["world_matrix"] is not None), None)
            if world is None:
                return None
            low = high = tuple(row[3] for row in world[:3])
        return {"anchor_m": tuple(lo / 2 + hi / 2 for lo, hi in zip(low, high)), "minimum_m": low, "maximum_m": high}


class RuleRun:
    def __init__(self, ctx, rule, severity_override):
        self.ctx, self.rule, self.override = ctx, rule, severity_override
        self.complete = True

    def finding(self, code, title, explanation, resolution, refs=(), *,
                severity=None, qualifier="", measurement=None, metadata=None, incomplete=False):
        self.complete = self.complete and not incomplete
        refs = tuple(sorted({ref for ref in refs if ref in self.ctx.by_ref}))
        identity = [(kind, str(uid)) for kind, uid in refs]
        key = json.dumps([str(self.ctx.scene.id), self.rule.id, code, identity, qualifier], separators=(",", ":"))
        finding_id = uuid5(_FINDING_NAMESPACE, key)
        if finding_id in self.ctx.findings:
            return
        severity = self.override or severity or self.rule.default_severity
        rows = self.ctx.by_ref
        entities = [{"id": uid, "type": kind, "name": rows[kind, uid].name,
                     "object_type": getattr(rows[kind, uid], "object_type", None)} for kind, uid in refs]
        zones = {uid for kind, uid in refs if kind == "zone"}
        for kind, uid in refs:
            zone_id = self.ctx.zones_by_object.get(uid) if kind in {"object", "group"} else None
            if zone_id is not None and ("zone", zone_id) in rows:
                zones.add(zone_id)
        if measurement is not None and any(not math.isfinite(value) for key, value in measurement.items()
                                           if key != "unit" and value is not None):
            measurement = None
        self.ctx.findings[finding_id] = {
            "id": finding_id, "rule_id": self.rule.id, "rule_name": self.rule.name, "category": self.rule.category,
            "code": code, "severity": severity, "blocking": severity == "error",
            "title": title, "explanation": explanation, "resolution": resolution,
            "entities": entities, "zone_ids": sorted(zones), "location": self.ctx.location(refs),
            "measurement": measurement, "metadata": metadata or {},
        }


def validate(db, scene_id, user_id, request: ValidationReportRequest, *, boundary_zone_ids=()):
    start, timestamp = perf_counter(), datetime.now(timezone.utc)
    scene = _get_scene(db, scene_id, user_id, lock="read")
    ctx = SceneValidation(db, scene, request, boundary_zone_ids=boundary_zone_ids)
    return evaluate_context(ctx, request, start=start, timestamp=timestamp)


def evaluate_context(ctx, request: ValidationReportRequest, *, start=None, timestamp=None, include_live_catalog=True):
    start = perf_counter() if start is None else start
    timestamp = timestamp or datetime.now(timezone.utc)
    scene = ctx.scene
    executed = []
    for rule in RULES:
        if rule.id == "object_library" and not include_live_catalog:
            continue
        options = ctx.config.rules.get(rule.id)
        if options is not None and options.enabled is False:
            continue
        run = RuleRun(ctx, rule, options.severity if options is not None else None)
        rule.check(ctx, run)
        executed.append({
            "id": rule.id, "name": rule.name, "category": rule.category, "default_severity": rule.default_severity,
            "severity_override": run.override, "description": rule.description, "complete": run.complete,
        })

    def order(finding):
        return (_SEVERITY_ORDER[finding["severity"]], finding["rule_id"], finding["code"],
                tuple((ref["type"], ref["id"]) for ref in finding["entities"]), finding["id"])

    findings = sorted(ctx.findings.values(), key=order)
    counts = Counter(finding["severity"] for finding in findings)
    complete = all(rule["complete"] for rule in executed)
    if counts["error"]:
        status = "invalid"
    elif not complete:
        status = "incomplete"
    else:
        status = "valid_with_findings" if findings else "valid"
    filtered = [finding for finding in findings if
                (request.severity is None or request.severity == finding["severity"])
                and (request.category is None or request.category == finding["category"])]
    return {
        "run_id": uuid4(), "scene_id": scene.id, "project_id": scene.project_id, "event_id": scene.event_id,
        "scene_revision": scene.current_revision, "checked_at": timestamp, "duration_ms": (perf_counter() - start) * 1000,
        "configuration_fingerprint": fingerprint(ctx.config),
        "findings_fingerprint": sha256(json.dumps(jsonable_encoder(findings), sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest(),
        "status": status, "complete": complete,
        "summary": {"total": len(findings), "errors": counts["error"], "warnings": counts["warning"], "info": counts["info"],
                    "blocking": counts["error"]},
        "rules_executed": executed, "offset": request.offset, "limit": request.limit,
        "findings": filtered[request.offset:request.offset + request.limit], "filtered_total": len(filtered),
    }
