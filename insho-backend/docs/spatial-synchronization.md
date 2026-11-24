# Phase 9 Part 6 — 2D and 3D synchronization

## Canonical source and pipeline

Part 1 stores the scene, object and zone transforms, geometry inputs and revision history. Part 2 resolves world transforms and geometric measurements. Part 3 reads a 2D editing snapshot and submits revision-checked writes. Part 4 supplies ordering, layers, hierarchy, inherited visibility and locks. Part 5 compiles the 3D snapshot. The individual read routes already shared canonical data, but separate requests could observe different committed revisions.

The synchronization service acquires one shared scene lock, reads the Part 4 graph, and runs the Part 5 compiler once. The two views use the same resolved world matrices and validated local footprint sources. The 2D view uses the shared ground projection in `app/spatial/projection.py`. The compiler then checks the pair and returns it as a single snapshot. Both representations are generated on demand. The service writes no geometry, cache entries or revision records.

The original editor, graph, geometry and 3D routes remain available. The Part 3 editor and Part 2 measurement APIs share the projection math with Part 6. Where those APIs currently use a footprint at local Z = 0 for editing and measurements, the paired view uses the complete solid's orthographic outline when it is supported. This preserves the established measurement and editing contracts.

## Coordinate and transform rules

| Field | Rule |
| --- | --- |
| Origin and axes | Right-handed, Z-up. The lower-left floor corner is the origin. +X is right/east, +Y is away/north, +Z is up. |
| Ground plane | XY at Z = 0. A plan point uses canonical world X and Y directly. A viewport handles pixel offsets, zoom and screen Y inversion. |
| Units | Geometry and translations are in meters. Depth is length along local Y and height is along local Z. `display_unit` and `meters_per_display_unit` are presentation metadata from the shared unit conversion utilities. |
| Rotation and scale | Rotation uses right-hand-rule degrees. Apply local scale, then rotations about X, Y and Z, then translation. Matrix order is `T * Rz * Ry * Rx * S` for column vectors. |
| Hierarchy | A world matrix is the transform parent's world matrix multiplied by the local matrix. Objects and groups inherit other object or group frames. Zones inherit zone frames. Assigning an object to a zone changes its graph placement and inherited state. It does not apply the zone's transform to that object. |
| Elevation and dimensions | Plan `world_origin_m` retains all three components of the world pivot. The plan retains authored width, depth and height. Projected extents and 3D mesh bounds are separate from those authored measurements. |

The plan projects the shape along world Z after resolving its full world matrix. Local offsets, tilted axes and nonuniform scales participate in the projection. The 3D renderer applies `world_matrix` once to the supplied local vertices. The two views use the same IDs, transform parents, tree parents, sibling and layer ranks and effective states.

## Shape mapping

| Canonical source | Paired 2D footprint | Part 5 3D geometry |
| --- | --- | --- |
| Box aliases or a measured zone without a polygon | Rectangle or exact polygon outline. For a tilted solid the outline uses projected top and bottom corners. | Centered box with authored width, depth and height. |
| Measured polygon object or zone | The transformed polygon when height adds no material XY extent. Tilted convex prisms use the exact hull of both projected caps. A tilted concave prism has an unsupported plan outline. | Polygon prism centered on local Z, including concave footprints. |
| Unmeasured zone with a polygon boundary | Projected polygon. An edge-on polygon surface has an unsupported plan outline. | Polygon surface at local Z = 0. |
| Circle | An exact circle if the projected axes preserve a circle and height adds no material XY extent. Elliptical and tilted cylinder outlines are unsupported. | A 64-segment cylinder with analytic world bounds. |
| Segment or point | World XY endpoints or origin. | Local segment or point placed by the world matrix. |
| Group or zone without measurements | No footprint. Unmeasured zones carry a warning. | No mesh. |
| Unknown or invalid geometry | Node retained with a diagnostic. | Node retained with a diagnostic. |

“Material” XY spread uses the Part 2 linear tolerance to treat sub-tolerance offsets as coincident. The same tolerance compares the 2D bounds with the corresponding XY extents of the 3D bounds. No canonical values are rounded or written by projection. The 2D footprint center is a shape measurement and can differ from its world pivot.

## HTTP contract

`GET /api/v1/spatial/scenes/{scene_id}/synchronization` requires the existing bearer session and project or event access. Readers in the owning organization can fetch an authorized scene. A missing or inaccessible scene returns `404` and unauthenticated requests return `401`. Responses use `Cache-Control: private, no-store`.

The response includes `schema_version`, `scene_id`, `scene_revision`, `freshness`, `validation`, `plan`, and `runtime`. Both views repeat the same scene ID and revision. The plan includes its world XY bounds, coordinate and unit metadata, the ordered roots, and nodes with projection, state and ordering. The runtime is the existing Part 5 schema with meshes, world frames, layer metadata, appearance and asset references. Join nodes using the typed pair `(type, id)`. Use `runtime.layers` for both views.

Pass optional `plan_revision` and `runtime_revision` query parameters to compare cached client views of this same scene. Each result gives the supplied revision and one of:

- `not_supplied` for a new view.
- `current` for a match with the canonical revision.
- `stale` for an older revision.
- `ahead` for a revision newer than the authorized scene snapshot. Verify scene identity before replacing local state.

The endpoint always returns the pair compiled at the current canonical revision, including when the input revisions disagree. It does not retrieve historical geometry. Canonical writes advance the existing scene revision. Each subsequent read recomputes both views, so creates, deletions, transforms, moves between parents, layer changes and visibility changes are reflected in the next pair. Freshness measures the scene revision observed during compilation. Another writer can commit after the read finishes.

### Validation

The result status is `synchronized` when the generated views agree without warnings, `partial` when warnings leave some geometry unavailable, or `invalid` when an error is present. Diagnostics carry severity, code, source representation, message, and where relevant a typed node ID, field or failed reference. Checks cover identity, revision, coordinates, units, ordered nodes, hierarchy, authored dimensions, layer assignment, visibility, world origins, and the XY extents of compatible geometry. Canonical invalid references, transforms, dimensions, and shapes also retain their Part 5 diagnostics.

A legitimate unsupported outline leaves the 2D footprint empty and can keep the same node drawable in 3D. A failed canonical shape compilation leaves that node present with diagnostics and disables rendering in both views. Hidden nodes remain in both payloads with their effective visibility and geometry where available. Locks do not change rendering eligibility. A pair consistency failure disables drawing of the affected plan nodes. The validator checks shape extents and shared metadata. It does not perform the separate Part 2 collision or clearance checks or reconstruct polygons from 3D meshes.

## Phase 12 write flow

1. Fetch one paired snapshot and attach its scene ID and revision to both views.
2. Draw supplied footprints or meshes for nodes whose view has `renderable: true`. Use `effective_locked` for edit affordances.
3. Submit canonical field changes with `PUT /api/v1/spatial/scenes/{scene_id}/editor/changes/{change_id}` or structural operations with `PATCH /api/v1/spatial/scenes/{scene_id}/graph`. Use the displayed revision as `base_revision`. Send only deliberate changes to canonical authored values.
4. After a successful write, fetch the paired snapshot again. A stale write returns `409`; fetch the latest pair before deciding whether to retry the edit.

The synchronization API is read-only. Future clients can use these write contracts for either editor. The backend authorizes and validates canonical commands, so view-local meshes, pixel data and computed bounds are never accepted as authoritative spatial updates.

## Implementation and verification

`app/spatial/projection.py` contains shared XY projection math. `app/services/spatial_sync.py` compiles and checks the pair. `app/schemas/spatial_sync.py` and `app/api/v1/spatial.py` define the public read contract. The existing geometry and runtime services provide canonical shape and world frame inputs.

```sh
./.venv/bin/python -m pytest -q -k 'not test_editor_real_browser'
./.venv/bin/python -m alembic check
```

The backend suite passed 137 tests, including seven synchronization cases. Tests cover byte-identical repeat reads, canonical revision preservation, tenant isolation, world transform and XY geometry agreement, unsupported shapes, stale reads and writes, and PostgreSQL writer exclusion during paired compilation. No schema migration or frontend change was required.
