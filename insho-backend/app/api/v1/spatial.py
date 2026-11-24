from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.object_library import InstantiateDefinition, LibraryPlacement
from app.schemas.spatial import (
    SpatialAssetAttach, SpatialAssetResponse, SpatialHierarchyNode,
    SpatialObjectCreate, SpatialObjectResponse, SpatialObjectUpdate,
    SpatialRelationshipCreate, SpatialRelationshipResponse,
    SpatialRevisionResponse, SpatialSceneCreate, SpatialSceneResponse,
    SpatialSceneUpdate, SpatialZoneCreate, SpatialZoneResponse,
    SpatialZoneUpdate,
)
from app.schemas.spatial_geometry import (
    AlignRequest, AlignResponse, ClearanceRequest, ClearanceResponse,
    Geometry2DResponse, SceneValidationRequest, SceneValidationResponse,
    SnapRequest, SnapResponse,
)
from app.schemas.spatial_graph import GraphChange, GraphChangeResult, NodeRelations, SceneGraph
from app.schemas.spatial_runtime import CompiledScene3D
from app.schemas.spatial_sync import SynchronizedScene
from app.schemas.spatial_validation import ValidationReport, ValidationReportRequest
from app.services import object_library, spatial_editor, spatial_graph, spatial_runtime, spatial_sync, spatial_validation
from app.services.spatial import SpatialService
from app.services.spatial_geometry import SpatialGeometryService
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Vec2


router = APIRouter(prefix="/spatial", tags=["Spatial Design"])


def _geometry_call(operation):
    try:
        return operation()
    except GeometryError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/scenes", response_model=SpatialSceneResponse, status_code=status.HTTP_201_CREATED)
def create_scene(project_id: UUID, data: SpatialSceneCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.create_scene(db, project_id, data, current_user.id)


@router.get("/projects/{project_id}/scenes", response_model=list[SpatialSceneResponse])
def list_scenes(project_id: UUID, event_id: Optional[UUID] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.list_scenes(db, project_id, current_user.id, event_id)


@router.get("/scenes/{scene_id}", response_model=SpatialSceneResponse)
def get_scene(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.get_scene(db, scene_id, current_user.id)


@router.patch("/scenes/{scene_id}", response_model=SpatialSceneResponse)
def update_scene(scene_id: UUID, data: SpatialSceneUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.update_scene(db, scene_id, data, current_user.id)


@router.delete("/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scene(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    SpatialService.delete_scene(db, scene_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/scenes/{scene_id}/zones", response_model=SpatialZoneResponse, status_code=status.HTTP_201_CREATED)
def create_zone(scene_id: UUID, data: SpatialZoneCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.create_zone(db, scene_id, data, current_user.id)


@router.get("/scenes/{scene_id}/zones", response_model=list[SpatialZoneResponse])
def list_zones(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.list_zones(db, scene_id, current_user.id)


@router.get("/zones/{zone_id}", response_model=SpatialZoneResponse)
def get_zone(zone_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.get_zone(db, zone_id, current_user.id)


@router.patch("/zones/{zone_id}", response_model=SpatialZoneResponse)
def update_zone(zone_id: UUID, data: SpatialZoneUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.update_zone(db, zone_id, data, current_user.id)


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    SpatialService.delete_zone(db, zone_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/scenes/{scene_id}/objects", response_model=SpatialObjectResponse, status_code=status.HTTP_201_CREATED)
def create_object(scene_id: UUID, data: SpatialObjectCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.create_object(db, scene_id, data, current_user.id)


@router.get("/scenes/{scene_id}/objects", response_model=list[SpatialObjectResponse])
def list_objects(scene_id: UUID, zone_id: Optional[UUID] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.list_objects(db, scene_id, current_user.id, zone_id)


@router.get("/objects/{object_id}", response_model=SpatialObjectResponse)
def get_object(object_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.get_object(db, object_id, current_user.id)


@router.patch("/objects/{object_id}", response_model=SpatialObjectResponse)
def update_object(object_id: UUID, data: SpatialObjectUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.update_object(db, object_id, data, current_user.id)


@router.delete("/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_object(object_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    SpatialService.delete_object(db, object_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/scenes/{scene_id}/hierarchy", response_model=SpatialHierarchyNode)
def get_hierarchy(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.hierarchy(db, scene_id, current_user.id)


@router.post("/scenes/{scene_id}/relationships", response_model=SpatialRelationshipResponse, status_code=status.HTTP_201_CREATED)
def create_relationship(scene_id: UUID, data: SpatialRelationshipCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.create_relationship(db, scene_id, data, current_user.id)


@router.get("/scenes/{scene_id}/relationships", response_model=list[SpatialRelationshipResponse])
def list_relationships(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.list_relationships(db, scene_id, current_user.id)


@router.delete("/relationships/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_relationship(relationship_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    SpatialService.delete_relationship(db, relationship_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/objects/{object_id}/assets", response_model=SpatialAssetResponse, status_code=status.HTTP_201_CREATED)
def attach_asset(object_id: UUID, data: SpatialAssetAttach, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.attach_asset(db, object_id, data, current_user.id)


@router.get("/objects/{object_id}/assets", response_model=list[SpatialAssetResponse])
def list_assets(object_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SpatialService.list_assets(db, object_id, current_user.id)


@router.delete("/objects/{object_id}/assets/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_asset(object_id: UUID, link_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    SpatialService.detach_asset(db, object_id, link_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/scenes/{scene_id}/revisions", response_model=list[SpatialRevisionResponse])
def list_revisions(
    scene_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SpatialService.revisions(db, scene_id, current_user.id, offset=offset, limit=limit)


@router.get("/scenes/{scene_id}/objects/{object_id}/geometry", response_model=Geometry2DResponse)
def get_object_geometry(scene_id: UUID, object_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _geometry_call(lambda: SpatialGeometryService.object_geometry(db, scene_id, object_id, current_user.id))


@router.post("/scenes/{scene_id}/validate", response_model=SceneValidationResponse)
def validate_scene(scene_id: UUID, data: SceneValidationRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tolerance = Tolerance(data.tolerance_m, DEFAULT_TOLERANCE.relative) if data.tolerance_m else DEFAULT_TOLERANCE
    return _geometry_call(lambda: SpatialGeometryService.validate_scene(db, scene_id, current_user.id, data.constraints, tolerance))


@router.post("/scenes/{scene_id}/validation-report", response_model=ValidationReport)
def validate_scene_report(scene_id: UUID, data: Optional[ValidationReportRequest] = None,
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_validation.validate(db, scene_id, current_user.id, data or ValidationReportRequest())


@router.post("/scenes/{scene_id}/snap", response_model=SnapResponse)
def snap_scene_point(scene_id: UUID, data: SnapRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = _geometry_call(lambda: SpatialGeometryService.snap(
        db, scene_id, current_user.id, Vec2(data.position.x_m, data.position.y_m),
        data.grid_spacing_m, data.anchor_tolerance_m, data.exclude_object_id,
        data.exclude_object_ids, data.include_hidden, data.use_anchors,
    ))
    return {"snapped": result.snapped,
            "point": {"x_m": result.point.x, "y_m": result.point.y},
            "target_id": result.target_id, "target_kind": result.target_kind,
            "distance_m": result.distance_m, "tolerance_m": result.tolerance_m}


@router.post("/scenes/{scene_id}/align", response_model=AlignResponse)
def align_scene_objects(scene_id: UUID, data: AlignRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _geometry_call(lambda: SpatialGeometryService.align_objects(
        db, scene_id, current_user.id, data.object_ids, data.mode, data.distribute_axis,
    ))


@router.post("/scenes/{scene_id}/clearance", response_model=ClearanceResponse)
def calculate_scene_clearance(scene_id: UUID, data: ClearanceRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _geometry_call(lambda: SpatialGeometryService.clearance(
        db, scene_id, data.first_object_id, data.second_object_id,
        current_user.id, data.requested_clearance_m,
    ))


@router.get("/scenes/{scene_id}/editor")
def editor_snapshot(scene_id: UUID, response: Response, current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return _geometry_call(lambda: spatial_editor.snapshot(db, scene_id, current_user.id))


@router.put("/scenes/{scene_id}/editor/changes/{change_id}")
def editor_change(scene_id: UUID, change_id: UUID, data: spatial_editor.EditorChange,
                  current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _geometry_call(lambda: spatial_editor.apply_change(db, scene_id, current_user.id, change_id, data))


@router.get("/scenes/{scene_id}/graph", response_model=SceneGraph)
def get_scene_graph(scene_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_graph.graph(db, scene_id, current_user.id)


@router.get("/scenes/{scene_id}/graph/nodes/{node_type}/{node_id}", response_model=NodeRelations)
def get_scene_graph_node(scene_id: UUID, node_type: Literal["object", "group", "zone"], node_id: UUID,
                         current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_graph.node_relations(db, scene_id, current_user.id, node_type, node_id)


@router.patch("/scenes/{scene_id}/graph", response_model=GraphChangeResult)
def update_scene_graph(scene_id: UUID, data: GraphChange,
                       current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return spatial_graph.apply(db, scene_id, current_user.id, data)


@router.get("/scenes/{scene_id}/runtime-3d", response_model=CompiledScene3D)
def get_scene_runtime_3d(scene_id: UUID, response: Response, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return spatial_runtime.compile_scene(db, scene_id, current_user.id)


@router.get("/scenes/{scene_id}/synchronization", response_model=SynchronizedScene)
def get_scene_synchronization(scene_id: UUID, response: Response,
                              plan_revision: Optional[int] = Query(default=None, ge=1),
                              runtime_revision: Optional[int] = Query(default=None, ge=1),
                              current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return spatial_sync.synchronize_scene(
        db, scene_id, current_user.id, plan_revision=plan_revision, runtime_revision=runtime_revision,
    )


@router.post("/scenes/{scene_id}/objects/from-library/{definition_id}", response_model=LibraryPlacement,
             status_code=status.HTTP_201_CREATED)
def instantiate_library_object(scene_id: UUID, definition_id: UUID, data: InstantiateDefinition,
                               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _geometry_call(lambda: object_library.instantiate(db, current_user.id, scene_id, definition_id, data))
