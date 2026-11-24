# Phase 9 Part 1: Spatial Architecture and Canonical Scene Model

## Purpose and ownership

The spatial domain is the canonical source for measured event-space design. A project can own multiple scenes, and a scene can optionally be scoped to one event from that same project. Existing `Venue` and `VenueZone` records continue to describe reusable venue information; they are not spatial scene rows and are not migrated or overwritten.

The project is the authorization boundary. Every scene read/write resolves its project through `require_project_access`; event-scoped scenes also resolve the event and verify its project and organization. Reads require membership and writes require an editor role under the existing organization role rules. A child resource is always resolved through its scene before returning data or applying a write. Unauthorized and cross-scene IDs are hidden as not found.

## Canonical entities

- **SpatialScene**: project/event ownership, name, description, open-ended scene type, lifecycle, display-unit preference, fixed coordinate-system identifier, canonical bounds, metadata, creator, timestamps, soft-delete state, and current revision number.
- **SpatialZone**: scene-local area with extensible category, optional parent zone, transform, optional dimensions, optional local polygon footprint, appearance, metadata, and status.
- **SpatialObject**: scene-local entity with extensible object type/category, optional zone and parent object, transform, required dimensions, appearance, material references, arbitrary object properties, tags, and status.
- **SpatialObjectRelationship**: typed directed edge between two objects in one scene. It supplements (and does not replace) the parent-child hierarchy.
- **SpatialObjectAsset**: a role-specific link from an object to an existing managed asset. It snapshots non-secret asset type/source/media metadata and available derivative kinds; binaries remain in the existing asset storage system.
- **SpatialSceneRevision**: append-only per-mutation history with actor, operation, entity type/id, before/after row state, revision number, and timestamp.

Composite foreign keys for zone/object parents, object-zone assignment, relationship endpoints, and asset links include `scene_id`. These database checks complement service validation and make cross-scene graph edges impossible even if a client supplies unrelated IDs. Parent and assigned-zone foreign keys restrict direct deletion; services check dependencies first and return `409`. Deleting an object removes its relationship and asset-link rows only after capturing those edges and links in the deletion revision. The underlying assets are never deleted by spatial operations.

## Coordinate, units, and transform rules

The sole stored coordinate system is `right_handed_z_up`:

- Origin: lower-left corner of the venue floor.
- +X: right/east on a plan.
- +Y: away from the origin/north on a plan.
- +Z: upward from the floor.
- Positive angle: right-hand rule about the named axis.
- Rotation: Euler degrees about X, Y, and Z, composed as `Rz * Ry * Rx` (apply local X, then Y, then Z).

All positions, bounds, dimensions, and polygon coordinates are canonical meters. Display units are `m`, `cm`, `mm`, `ft`, and `in`; `display_unit` is presentation metadata only. The conversion functions in `app/spatial_units.py` use `Decimal` and exact conversion constants. Converting to display units does not write back to the database.

Object and zone transforms store local position (meters), local Euler rotation (degrees), and positive dimensionless scale. Parentless transforms are relative to the scene; child transforms are relative to the parent. Part 2's pure engine resolves composed world matrices for geometry calculations. Object position is the local pivot and box/rectangle dimensions extend symmetrically around that pivot. Dimensions are authored, unscaled local extents in meters. Effective extents are calculated as `dimension * corresponding scale` and returned as `effective_dimensions_m`; scale does not change stored dimensions. Rotation changes orientation but does not rewrite the authored dimensions.

Zone footprints are optional ordered local XY polygon point arrays in meters. Part 1's schema validates finite coordinates and at least three points. The Part 2 engine additionally validates polygon uniqueness, nonzero area, and self-intersection when geometry is evaluated, and supplies containment, snapping, collision, and bounds calculations.

## Revision and transaction behavior

Scene creation starts at revision 1. Every subsequent successful spatial mutation locks the owning scene row, increments `current_revision`, writes exactly one revision record, and commits the entity mutation and audit row in the same transaction. This serializes writes within a scene and gives each mutation a stable identity. Before/after state captures only the changed entity row instead of copying an entire scene. Object deletion additionally captures its attached relationship and asset-link rows before the database cascades them.

The revisions endpoint is read-only and paginated. Part 1 intentionally does not expose restore or undo because a complete restore contract must account for later entity edits and asset availability. Scene deletion is a soft delete that preserves its history and is allowed only after zones and objects have been removed.

## API contract

Routes are mounted under `/api/v1/spatial`; detailed methods are listed in the backend README. Request transforms look like:

```json
{
  "position": {"x": 1.25, "y": 3, "z": 0},
  "rotation_deg": {"x": 0, "y": 0, "z": 90},
  "scale": {"x": 1, "y": 1, "z": 1}
}
```

`position` values and scene/object dimensions are always meters regardless of the scene's display unit. Scene bounds are required and positive. Object dimensions are required and positive. Zone dimensions can be omitted when the area is represented by a footprint or is not yet measured. Scales must be finite and positive. Schemas reject unknown fields. Categories and object types are normalized as strings and are not restricted to a catalog, so product-specific vocabularies can grow without a schema migration.

Hierarchy is represented in two ways: zone parent links and object parent links. The hierarchy read composes a scene tree and places top-level assigned objects under their zone; object children remain under their object parent. Service checks reject self-parenting, cycles, and cross-scene parents. Typed relationship edges are separately created and removed through the relationship endpoints.

## Asset integration

Asset attachment calls the existing `AssetService.get`, then verifies organization, project, workspace, and event compatibility with the scene. A project- or event-scoped asset cannot be attached to a different project or event. An organization/workspace asset can be attached only within its authorized organization/workspace. The link records an asset ID and metadata snapshot (category, MIME type, extension, storage provider, media metadata, derivative kinds); it does not expose storage keys, create binaries, or claim a model file exists. `model`, `preview`, `material`, `texture`, and `reference` are supported link roles.

## Phase boundaries

This Part 1 implementation supplies persistence, tenant-safe APIs, transforms as canonical data, hierarchy, relationships, existing-asset links, and audit history. Part 2 supplies deterministic geometry and validation in a separate pure engine. Neither part implements the editor, renderer, 3D visualization, synchronization, object catalog, AI spatial validation, or automatic mapping from Phase 8 design artifacts. Future consumers must read/write this model rather than maintain separate conflicting layout state.

See [the Phase 9 Part 2 geometry guide](spatial-engine.md) for implemented calculations and their boundaries.
