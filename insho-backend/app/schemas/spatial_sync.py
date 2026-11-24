"""Stable contracts for reading both projections at one scene revision."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, FiniteFloat

from app.schemas.spatial_geometry import GeometryPoint2D
from app.schemas.spatial_graph import NodeRef
from app.schemas.spatial_runtime import CompiledScene3D, Dimensions3D, RuntimeIssue, Vector


class PlanBounds2D(BaseModel):
    min_x_m: FiniteFloat
    min_y_m: FiniteFloat
    max_x_m: FiniteFloat
    max_y_m: FiniteFloat
    width_m: FiniteFloat
    depth_m: FiniteFloat


class OrientedBounds2D(BaseModel):
    center: GeometryPoint2D
    width_m: FiniteFloat
    depth_m: FiniteFloat
    rotation_deg: FiniteFloat


class Footprint2D(BaseModel):
    geometry_type: Literal["rectangle", "polygon", "circle", "line_segment", "point"]
    center: GeometryPoint2D
    bounds: PlanBounds2D
    vertices: list[GeometryPoint2D] = Field(default_factory=list)
    radius_m: Optional[FiniteFloat] = None
    oriented_bounds: Optional[OrientedBounds2D] = None
    area_m2: FiniteFloat
    perimeter_m: FiniteFloat


class PlanNode2D(NodeRef):
    library_definition_id: Optional[UUID] = None
    library_definition_version: Optional[int] = None
    parent: Optional[NodeRef]
    transform_parent: Optional[NodeRef]
    children: list[NodeRef]
    order_index: int
    zone_id: Optional[UUID]
    layer_id: Optional[UUID]
    layer_order_index: Optional[int]
    visible: bool
    locked: bool
    effective_visible: bool
    effective_locked: bool
    authored_dimensions: Optional[Dimensions3D]
    world_origin_m: Optional[Vector]
    footprint: Optional[Footprint2D]
    renderable: bool


class Projection2D(BaseModel):
    scene_id: UUID
    scene_revision: int
    coordinate_system: Literal["right_handed_z_up"]
    length_unit: Literal["m"]
    display_unit: str
    meters_per_display_unit: Optional[FiniteFloat]
    default_layer_id: Optional[UUID]
    bounds: Optional[PlanBounds2D]
    roots: list[NodeRef]
    nodes: list[PlanNode2D]


class RevisionCheck(BaseModel):
    supplied_revision: Optional[int]
    status: Literal["not_supplied", "current", "stale", "ahead"]


class RevisionChecks(BaseModel):
    plan: RevisionCheck
    runtime: RevisionCheck


class SynchronizationIssue(RuntimeIssue):
    representation: Literal["plan", "runtime", "pair"]


class SynchronizationValidation(BaseModel):
    status: Literal["synchronized", "partial", "invalid"]
    issues: list[SynchronizationIssue]


class SynchronizedScene(BaseModel):
    schema_version: Literal[1] = 1
    scene_id: UUID
    scene_revision: int
    freshness: RevisionChecks
    validation: SynchronizationValidation
    plan: Projection2D
    runtime: CompiledScene3D
