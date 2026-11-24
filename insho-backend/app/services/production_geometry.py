"""Read canonical Phase 9 geometry once per production operation."""

from dataclasses import asdict
from hashlib import sha256
import json
from uuid import UUID

from app.models.spatial_design_review import SpatialDesignReview
from app.services.spatial_graph import GraphRead
from app.services.spatial_runtime import SceneCompilation
from app.spatial.math3d import IDENTITY


AXES = {"x": "width_m", "y": "depth_m", "z": "height_m"}
DIMENSIONS = {"width": "width_m", "length": "depth_m", "height": "height_m"}


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def target_id(item, scene, target):
    return {"scene": scene.id, "zone": item.spatial_zone_id, "object": item.spatial_object_id}[target]


class ProductionGeometry:
    def __init__(self, db, scene):
        self.db = db
        self.scene = scene
        self.compilation = None
        self.nodes = {}

    def _load(self):
        if self.compilation is None:
            self.compilation = SceneCompilation(GraphRead(self.db, self.scene))
            self.nodes = {(node["type"], node["id"]): node for node in self.compilation.compile()["nodes"]}

    def node(self, target, source_id):
        if target in ("object", "zone") and source_id is not None:
            self._load()
            return self.nodes.get((target, source_id))

    def read(self, target, source_id, *, count=False):
        """Return reproducible physical inputs, excluding display state and translation."""
        if self.scene.deleted_at is not None or source_id is None:
            return {"state": "missing"}, None, IDENTITY
        if target == "scene":
            return {
                "state": "measured", "kind": "scene", "length_unit": "m",
                "authored_dimensions": {field: str(getattr(self.scene, field)) for field in AXES.values()},
            }, None, IDENTITY
        node = self.node(target, source_id)
        if node is None:
            return {"state": "missing"}, None, IDENTITY
        if node["status"] == "archived":
            return {"state": "archived"}, None, IDENTITY
        if count:
            return {"state": "placed", "instances": 1}, None, IDENTITY
        key = (target, source_id)
        if node["geometry"] is None or key in self.compilation.invalid_nodes:
            return {"state": "unmeasured"}, None, IDENTITY

        footprint = self.compilation.footprints[key]
        # Physical lengths, projected areas and volume are independent of translation.
        world = node["world_matrix"]
        linear_world = tuple((*row[:3], 0.0) for row in world[:3]) + (IDENTITY[3],)
        geometry = node["geometry"]
        return {
            "state": "measured", "kind": geometry["kind"], "length_unit": "m",
            "local_bounds": geometry["local_bounds"]["dimensions_m"],
            "authored_dimensions": node["authored_dimensions"],
            "local_footprint": asdict(footprint), "linear_transform": linear_world,
        }, footprint, linear_world

    def dimensions(self, target, source_id):
        if target == "scene":
            return {kind: getattr(self.scene, field) for kind, field in DIMENSIONS.items()}
        node = self.node(target, source_id)
        if node is None or node["geometry"] is None or (target, source_id) in self.compilation.invalid_nodes:
            return {}
        dimensions = node["geometry"]["world_bounds"]["dimensions_m"]
        return {kind: dimensions[field] for kind, field in DIMENSIONS.items()}

    def provenance(self, item, target, source_id):
        source = {"target": target, "target_id": str(source_id)}
        if target == "object" and item.source.get("placed_library_definition_version") is not None:
            source["library_definition_id"] = item.source["library_definition_id"]
            source["placed_library_definition_version"] = item.source["placed_library_definition_version"]
        node = self.node(target, source_id)
        origin = node["metadata"].get("design_intelligence") if node is not None else None
        if not isinstance(origin, dict):
            return source
        try:
            recommendation_id = UUID(origin["recommendation_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            return source
        review = self.db.get(SpatialDesignReview, (self.scene.id, recommendation_id))
        if (review is not None and review.status == "applied"
                and review.outcome.get("entity_id") == str(source_id) and review.outcome.get("entity_type") == target):
            source["design"] = {**review.source, "recommendation_id": str(recommendation_id),
                                "review_id": str(review.review_id)}
        return source
