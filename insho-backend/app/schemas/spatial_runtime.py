"""Versioned read contract for the canonical 3D scene runtime."""

from typing import Any, Literal, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel, FiniteFloat

from app.schemas.spatial_graph import GraphLayer, GraphNode, NodeRef


Vector = Tuple[FiniteFloat, FiniteFloat, FiniteFloat]
MatrixRow = Tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
Matrix = Tuple[MatrixRow, MatrixRow, MatrixRow, MatrixRow]


class Dimensions3D(BaseModel):
    width_m: FiniteFloat
    depth_m: FiniteFloat
    height_m: FiniteFloat


class Bounds3D(BaseModel):
    minimum_m: Vector
    maximum_m: Vector
    dimensions_m: Dimensions3D


class Coordinates3D(BaseModel):
    system: Literal["right_handed_z_up"] = "right_handed_z_up"
    handedness: Literal["right"] = "right"
    up_axis: Literal["Z"] = "Z"
    ground_plane: Literal["XY"] = "XY"
    x_axis: Literal["right_east"] = "right_east"
    y_axis: Literal["away_north"] = "away_north"
    origin: Literal["lower_left_scene_floor"] = "lower_left_scene_floor"
    length_unit: Literal["m"] = "m"
    angle_unit: Literal["deg"] = "deg"
    positive_rotation: Literal["right_hand_rule"] = "right_hand_rule"
    rotation_order: Literal["X_Y_Z"] = "X_Y_Z"
    matrix_layout: Literal["rows_column_vectors"] = "rows_column_vectors"


class LocalFrame3D(BaseModel):
    position_m: Vector
    rotation_deg: Vector
    scale: Vector
    matrix: Matrix


class Mesh3D(BaseModel):
    kind: Literal["box", "polygon_prism", "polygon_surface", "cylinder", "line_segment", "point"]
    mode: Literal["triangles", "lines", "points"]
    vertices_m: list[Vector]
    indices: list[int]
    local_bounds: Bounds3D
    world_bounds: Bounds3D


class RuntimeNode3D(GraphNode):
    object_type: Optional[str]
    transform_parent: Optional[NodeRef]
    authored_dimensions: Optional[Dimensions3D]
    appearance: dict[str, Any]
    material_refs: list[dict[str, Any]]
    local_transform: Optional[LocalFrame3D]
    world_matrix: Optional[Matrix]
    geometry: Optional[Mesh3D]
    renderable: bool


class RuntimeIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    node: Optional[NodeRef] = None
    field: Optional[str] = None
    reference_id: Optional[UUID] = None


class RuntimeValidation(BaseModel):
    status: Literal["valid", "valid_with_warnings", "invalid"]
    issues: list[RuntimeIssue]


class CompiledScene3D(BaseModel):
    schema_version: Literal[1] = 1
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    scene_revision: int
    name: str
    description: Optional[str]
    scene_type: str
    status: str
    metadata: dict[str, Any]
    coordinates: Coordinates3D
    source_coordinate_system: str
    display_unit: str
    meters_per_display_unit: Optional[FiniteFloat]
    dimensions: Optional[Dimensions3D]
    bounds: Optional[Bounds3D]
    default_layer_id: Optional[UUID]
    roots: list[NodeRef]
    nodes: list[RuntimeNode3D]
    layers: list[GraphLayer]
    validation: RuntimeValidation
