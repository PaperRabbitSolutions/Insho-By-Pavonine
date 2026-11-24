from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GeometryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeometryPoint2D(GeometryInput):
    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)


class GeometryPoint3D(GeometryInput):
    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)
    z_m: float = Field(allow_inf_nan=False)


class GeometryBounds2D(GeometryInput):
    min_x_m: float
    min_y_m: float
    max_x_m: float
    max_y_m: float
    width_m: float
    depth_m: float


class GeometryBounds3D(GeometryBounds2D):
    min_z_m: float
    max_z_m: float
    height_m: float


class Geometry2DResponse(GeometryInput):
    scene_id: UUID
    scene_revision: int
    object_id: UUID
    geometry_type: str
    center: GeometryPoint2D
    center_3d: GeometryPoint3D
    bounds: GeometryBounds2D
    vertices: list[GeometryPoint2D] = Field(default_factory=list)
    radius_m: Optional[float] = None
    oriented_bounds: Optional[dict[str, Any]] = None
    area_m2: float
    perimeter_m: float
    upright: bool
    volume_m3: float
    volume_corners: list[GeometryPoint3D]
    bounds_3d: GeometryBounds3D
    transform_matrix: list[list[float]]


class ConstraintSpec(GeometryInput):
    id: Optional[str] = Field(default=None, max_length=100)
    type: str = Field(min_length=1, max_length=80)
    subject_object_id: UUID
    related_object_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    minimum_distance_m: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    axis: Optional[str] = Field(default=None, pattern="^(x|y|z)$")
    tolerance_m: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    minimum: Optional[GeometryPoint3D] = None
    maximum: Optional[GeometryPoint3D] = None
    severity: Optional[str] = Field(default=None, max_length=30)


class SceneValidationRequest(GeometryInput):
    constraints: list[ConstraintSpec] = Field(default_factory=list, max_length=500)
    tolerance_m: Optional[float] = Field(default=None, gt=0, le=0.01, allow_inf_nan=False)


class SceneValidationResponse(GeometryInput):
    scene_id: UUID
    scene_revision: int
    evaluated_at: datetime
    status: str
    boundary_checks: list[dict[str, Any]]
    zone_checks: list[dict[str, Any]]
    overlaps: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    invalid_geometries: list[dict[str, Any]]
    tolerance_m: float


class SnapRequest(GeometryInput):
    position: GeometryPoint2D
    grid_spacing_m: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    anchor_tolerance_m: float = Field(default=0, ge=0, allow_inf_nan=False)
    exclude_object_id: Optional[UUID] = None
    exclude_object_ids: list[UUID] = Field(default_factory=list, max_length=500)
    include_hidden: bool = True
    use_anchors: bool = True


class SnapResponse(GeometryInput):
    snapped: bool
    point: GeometryPoint2D
    target_id: Optional[str]
    target_kind: Optional[str]
    distance_m: Optional[float]
    tolerance_m: float


class AlignRequest(GeometryInput):
    object_ids: list[UUID] = Field(min_length=2, max_length=500)
    mode: str = Field(default="center_x", pattern="^(left|right|center_x|top|bottom|center_y|center)$")
    distribute_axis: Optional[str] = Field(default=None, pattern="^(x|y)$")


class AlignResult(GeometryInput):
    object_id: UUID
    x_m: float
    y_m: float


class AlignResponse(GeometryInput):
    positions: list[AlignResult]
    persisted: bool


class ClearanceRequest(GeometryInput):
    first_object_id: UUID
    second_object_id: UUID
    requested_clearance_m: float = Field(ge=0, allow_inf_nan=False)


class ClearanceResponse(GeometryInput):
    first_id: str
    second_id: str
    distance_m: float
    requested_clearance_m: float
    clearance_met: bool
    tolerance_m: float
    calculation_method: str
    regulatory_assessment: bool
