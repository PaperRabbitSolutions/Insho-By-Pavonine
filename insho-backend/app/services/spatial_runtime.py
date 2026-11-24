"""Compile a consistent revision of the canonical scene and Part 4 graph."""

import math

from fastapi import HTTPException

from app.models.spatial import SpatialLayer, SpatialObject, SpatialZone
from app.schemas.spatial_runtime import Coordinates3D
from app.services.spatial import _get_scene
from app.services.spatial_geometry import _float, _local_transform, _object_local_footprint, _zone_local_footprint
from app.services.spatial_graph import GraphRead, _key, _ref, transform_parent_key
from app.services.spatial_policy import EffectiveState, state_sources
from app.spatial.math3d import resolve_world_matrices, transform_matrix
from app.spatial.runtime3d import compile_geometry
from app.spatial.types import GeometryError, UnsupportedGeometryError
from app.spatial.validation import scene_boundary
from app.spatial_units import to_meters


_COORDINATES = Coordinates3D()
_DIMENSION_KEYS = ("width_m", "depth_m", "height_m")
_STATE_REFERENCE_FIELDS = {
    SpatialObject: "parent_object_id", SpatialZone: "zone_id", SpatialLayer: "layer_id",
}


def _tuple(vector):
    return (vector.x, vector.y, vector.z)


def _dimensions(row, *, optional=False):
    values = [getattr(row, key) for key in _DIMENSION_KEYS]
    if optional and all(value is None for value in values):
        return None
    values = [_float(value) for value in values]
    if any(value <= 0 for value in values):
        raise GeometryError("Dimensions must be positive")
    return dict(zip(_DIMENSION_KEYS, values))


def _bounds(bounds):
    return {
        "minimum_m": (bounds.min_x, bounds.min_y, bounds.min_z),
        "maximum_m": (bounds.max_x, bounds.max_y, bounds.max_z),
        "dimensions_m": dict(zip(_DIMENSION_KEYS, _tuple(bounds.dimensions))),
    }


def _mesh_payload(mesh):
    return {
        "kind": mesh.kind, "mode": mesh.mode,
        "vertices_m": [_tuple(vertex) for vertex in mesh.vertices], "indices": mesh.indices,
        "local_bounds": _bounds(mesh.local_bounds), "world_bounds": _bounds(mesh.world_bounds),
    }


def _clean_json(value):
    """Preserve the JSON structure while sanitizing values too large for a JSON float."""
    if isinstance(value, float) and not math.isfinite(value):
        return None, True
    if isinstance(value, dict):
        entries = [(key, *_clean_json(item)) for key, item in value.items()]
        return {key: item for key, item, _ in entries}, any(changed for _, _, changed in entries)
    if isinstance(value, list):
        entries = [_clean_json(item) for item in value]
        return [item for item, _ in entries], any(changed for _, changed in entries)
    return value, False


class SceneCompilation:
    def __init__(self, read):
        self.read = read
        self.scene = read.scene
        self.issues = []
        self.invalid_nodes = set()
        self.footprints = {}
        self.projected_footprints = {}

    def issue(self, code, message, *, row=None, field=None, reference_id=None, severity="error"):
        self.issues.append({
            "code": code, "message": message, "severity": severity, "field": field,
            "node": _ref(row) if row is not None else None, "reference_id": reference_id,
        })
        if row is not None and severity == "error":
            self.invalid_nodes.add(_key(row))

    def json_field(self, value, field, *, row=None, array=False):
        valid = (isinstance(value, list) and all(isinstance(entry, dict) for entry in value)) if array else isinstance(value, dict)
        if not valid:
            self.issue("invalid_extension_data", "Invalid optional JSON data was omitted.",
                       row=row, field=field, severity="warning")
            return [] if array else {}
        normalized, changed = _clean_json(value)
        if changed:
            self.issue("non_finite_extension_data", "Non-finite values in optional JSON data were replaced with null.",
                       row=row, field=field, severity="warning")
        return normalized

    def transforms(self, rows):
        parents, local, matrices = {}, {}, {}
        for row in rows:
            key = _key(row)
            parents[key] = transform_parent_key(row)
            try:
                transform = _local_transform(row)
                matrix = transform_matrix(transform)
                matrices[key] = matrix
                local[key] = {
                    "position_m": _tuple(transform.position), "rotation_deg": _tuple(transform.rotation_deg),
                    "scale": _tuple(transform.scale), "matrix": matrix,
                }
            except GeometryError as exc:
                self.issue("invalid_transform", str(exc), row=row, field="transform")

        def report(key, code, message):
            row = self.read.by_key[key]
            field = "parent_zone_id" if isinstance(row, SpatialZone) else "parent_object_id"
            parent = parents[key]
            self.issue(code, message, row=row, field=field, reference_id=parent[1] if parent is not None else None)

        return local, resolve_world_matrices(matrices, parents, report=report)

    def state(self, row):
        missing = False
        for model, target_id in state_sources(row):
            if target_id is None or (model, target_id) in self.read.by_key:
                continue
            missing = True
            if transform_parent_key(row) == (model, target_id):
                # The shared transform resolver already reports missing transform parents.
                continue
            field = _STATE_REFERENCE_FIELDS[model]
            self.issue("missing_reference", f"The referenced {field} is absent from this scene.",
                       row=row, field=field, reference_id=target_id)
        if missing:
            return EffectiveState(visible=False, locked=True)

        # The graph's shared resolver refuses missing or cyclic dependencies.
        try:
            return self.read.state.resolve(row)
        except (KeyError, HTTPException):
            if _key(row) not in self.invalid_nodes:
                self.issue("effective_state_unavailable", "An inherited visibility or lock dependency is invalid.", row=row)
            return EffectiveState(visible=False, locked=True)

    def geometry(self, row, world):
        is_zone = isinstance(row, SpatialZone)
        try:
            dimensions = _dimensions(row, optional=is_zone)
        except GeometryError as exc:
            self.issue("invalid_dimensions", str(exc), row=row, field="dimensions")
            return None, None
        try:
            footprint = _zone_local_footprint(row) if is_zone else _object_local_footprint(row)
            if footprint is None:
                self.issue("unmeasured_zone", "The zone has no measured dimensions or footprint.",
                           row=row, field="geometry", severity="warning")
            elif world is not None:
                height = dimensions["height_m"] if dimensions is not None else None
                geometry = _mesh_payload(compile_geometry(footprint, height, world))
                identity = _ref(row)
                self.footprints[identity["type"], identity["id"]] = footprint
                return dimensions, geometry
        except UnsupportedGeometryError as exc:
            self.issue("unsupported_geometry", str(exc), row=row, field="geometry")
        except GeometryError as exc:
            self.issue("invalid_geometry", str(exc), row=row, field="geometry")
        except OverflowError:
            self.issue("invalid_geometry", "World geometry exceeds the supported numeric range.", row=row, field="geometry")
        return dimensions, None

    def node(self, row, local, world, scene_ready):
        key = _key(row)
        is_object = isinstance(row, SpatialObject)
        parent = transform_parent_key(row)
        resolved_parent = self.read.by_key.get(parent)
        state = self.state(row)
        node = self.read.node(row, state=state)
        dimensions, geometry = (None, None) if is_object and row.node_kind == "group" else self.geometry(row, world.get(key))
        return {
            **node, "metadata": self.json_field(node["metadata"], "metadata", row=row),
            "object_type": row.object_type if is_object else None,
            "transform_parent": _ref(resolved_parent) if resolved_parent is not None else None,
            "appearance": self.json_field(row.appearance, "appearance", row=row),
            "material_refs": self.json_field(row.material_refs, "material_refs", row=row, array=True) if is_object else [],
            "authored_dimensions": dimensions, "local_transform": local.get(key), "world_matrix": world.get(key),
            "geometry": geometry,
            "renderable": scene_ready and geometry is not None and state.visible and key not in self.invalid_nodes,
        }

    def compile(self):
        scene = self.scene
        coordinates_valid = scene.coordinate_system == _COORDINATES.system
        if not coordinates_valid:
            self.issue("invalid_coordinate_system", "The stored coordinate system is unsupported.", field="coordinate_system")
        dimensions, bounds = None, None
        try:
            dimensions = _dimensions(scene)
            bounds = _bounds(scene_boundary(**dimensions).aabb)
        except GeometryError as exc:
            self.issue("invalid_scene_dimensions", str(exc), field="dimensions")

        display_factor = None
        try:
            display_factor = float(to_meters(1, scene.display_unit))
        except ValueError:
            self.issue("unsupported_display_unit", "The display unit is unsupported. Geometry uses meters.",
                       field="display_unit", severity="warning")

        ordered = list(self.read.preorder())
        reached = {_key(row) for row in ordered}
        for row in self.read.nodes:
            if _key(row) not in reached:
                ordered.append(row)
                self.issue("hierarchy_unreachable", "The node cannot be reached from a scene root.", row=row, field="parent")
        local, world = self.transforms(ordered)
        if not coordinates_valid:
            world = {}
        nodes = [self.node(row, local, world, coordinates_valid and bounds is not None) for row in ordered]
        layers = self.read.layer_payloads()
        for layer in layers:
            layer["metadata"] = self.json_field(layer["metadata"], f"layers.{layer['id']}.metadata")
        metadata = self.json_field(scene.metadata_json, "metadata")
        status = "invalid" if any(issue["severity"] == "error" for issue in self.issues) else (
            "valid_with_warnings" if self.issues else "valid")
        return {
            "schema_version": 1, "scene_id": scene.id, "project_id": scene.project_id, "event_id": scene.event_id,
            "scene_revision": scene.current_revision, "name": scene.name, "description": scene.description,
            "scene_type": scene.scene_type, "status": scene.status, "metadata": metadata,
            "coordinates": _COORDINATES, "source_coordinate_system": scene.coordinate_system,
            "display_unit": scene.display_unit, "meters_per_display_unit": display_factor,
            "dimensions": dimensions, "bounds": bounds, "default_layer_id": next(
                (layer["id"] for layer in layers if layer["is_default"]), None),
            "roots": [_ref(row) for row in self.read.children[None]], "nodes": nodes, "layers": layers,
            "validation": {"status": status, "issues": self.issues},
        }


def compile_scene(db, scene_id, user_id):
    scene = _get_scene(db, scene_id, user_id, lock="read")
    return SceneCompilation(GraphRead(db, scene)).compile()
