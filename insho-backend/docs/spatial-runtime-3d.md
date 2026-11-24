# Phase 9 Part 5 — 3D Scene Engine

## Architecture and lifetime

Part 1 stores scene dimensions, object and zone transforms, footprints and asset associations. Part 2 supplies the shared footprint validation, affine transform math, bounds and polygon triangulation. Part 4 supplies the ordered graph, layers and inherited state. The existing object geometry endpoint serves individual footprints and box envelopes. The Part 5 compiler produces a scene-wide snapshot with renderer-independent meshes and diagnostics.

The read service acquires the same shared scene lock as the Part 4 graph API, loads the authorized canonical revision and traverses its roots and children in depth-first order. It validates local fields, resolves parent matrices, compiles supported geometry, and emits node state and diagnostics. Unreachable nodes are appended in canonical order and reported. There is no database write, derived table, or cache. Requests for the same revision and runtime version produce the same result. Any timestamps on nodes and layers are their stored timestamps.

## Coordinates and transforms

| Property | Convention |
| --- | --- |
| Coordinate system | `right_handed_z_up` |
| Origin | Lower-left corner of the scene floor |
| Axes | +X right/east, +Y away/north, +Z up |
| Ground plane | XY at Z = 0 |
| Units | Lengths are meters, rotations are degrees, scale is dimensionless |
| Rotation | Right-hand rule, applied about X, then Y, then Z |
| Matrix layout | Four JSON rows multiplied by column vectors |

A local matrix is `T * Rz * Ry * Rx * S`. Its world matrix is `parent_world * local`, or the local matrix if it has no transform parent. Object and group parents supply object frames. Zone parents supply zone frames. An object's zone assignment can place it in the tree and affect inherited state while its transform stays in the scene or object parent's frame. `parent` is the Part 4 tree reference. `transform_parent` identifies the frame used for geometry.

`display_unit` and `meters_per_display_unit` describe presentation only. Divide a length in meters by that factor to display it, and multiply an authored display length by the factor before sending a value in meters. Scene bounds run from `[0, 0, 0]` to `[width, depth, height]`. A Y-up renderer can convert the completed world coordinates at its boundary, for example `(x, y, z) → (x, z, -y)` preserves handedness. Keep that display conversion out of canonical data.

## Geometry mappings

All vertices are in the node's local coordinates. Indices are zero-based and grouped in triples for triangles, pairs for lines, and single indices for points. Solid triangles face outward. Polygon surface triangles face local +Z. Each geometry reports local and world axis-aligned bounds, including minimum, maximum and XYZ extents. Bounds for a rotated or sheared mesh are calculated from transformed vertices. Cylinder bounds enclose the exact smooth shape as described below.

| Canonical source | Runtime kind | Dimensions and validation |
| --- | --- | --- |
| Object `box`, `rectangle`, `rect`, or the existing default shape | `box` | Positive authored width, depth and height. The local pivot is the center. |
| Object `polygon` | `polygon_prism` | Valid local XY vertices inside authored width and depth. Shared Part 2 triangulation handles simple concave polygons. Height extends equally above and below local Z = 0. |
| Object `circle` | `cylinder` | Canonical radius, or half the authored width, must fit the authored XY extents. Height extends equally above and below local Z = 0. A fixed 64-segment closed mesh goes through the full affine world matrix, including tilt, unequal scales and shear. The world bounds use the analytic extent of the smooth cylinder. |
| Object `line` or `line_segment` | `line_segment` | Distinct canonical endpoints within the authored extents at local Z = 0. |
| Object `point` | `point` | Local origin. |
| Zone with dimensions and no footprint | `box` | Centered volume with the authored width, depth and height. |
| Zone with polygon footprint | `polygon_prism` or `polygon_surface` | A valid local XY polygon with a measured height becomes a centered prism. A footprint by itself produces a surface at local Z = 0. |
| Group or unmeasured zone | No mesh | Groups carry transforms and state. Unmeasured zones retain their place in the graph and receive a warning. |

Authored dimensions remain separate from measured mesh bounds. A tilted cylinder can render in this contract even when a Part 2 XY circle or overlap query cannot calculate that geometry. Managed asset links stay as authorized references with link ID, asset ID and role. Imported mesh parsing, geometry that requires holes or curved polygon edges, material evaluation, lighting and picking are outside the runtime contract. Unsupported authored geometry receives a diagnostic and an empty geometry slot.

## Validation and HTTP

`GET /api/v1/spatial/scenes/{scene_id}/runtime-3d` uses the existing bearer authentication and project/event permission checks. Members can read scenes they can access. Unauthorized or missing scene IDs follow the existing `401` and `404` behavior. The response is private and marked `no-store`.

The body contains `schema_version`, scene/project/event IDs, revision, scene metadata, coordinate and unit information, dimensions and bounds, ordered roots and nodes, layers with ordered members and a default layer ID, and a validation result. Nodes carry canonical IDs, category, type, local and inherited visibility/lock, local TRS and matrix, world matrix, zone, layer, order, appearance, material references, assets and geometry. Tree references use the same typed UUID format as Part 4.

Authorized scenes return a structured snapshot even when a node is invalid. Each diagnostic includes `severity`, `code`, `message` and, when available, the typed node, field and failed reference ID. Typical error codes are `invalid_coordinate_system`, `invalid_scene_dimensions`, `hierarchy_unreachable`, `missing_parent`, `missing_reference`, `parent_cycle`, `invalid_transform`, `invalid_world_transform`, `parent_transform_unavailable`, `invalid_dimensions`, `unsupported_geometry`, and `invalid_geometry`. One invalid branch leaves independent branches available. A bad coordinate system suppresses world geometry. Invalid scene bounds suppress rendering. The overall validation status is `valid`, `valid_with_warnings`, or `invalid`.

## Phase 12 consumption

Use the typed IDs to match the layout, tree and 3D views at the same `scene_revision`. For each node with `renderable: true`, upload its local vertices and topology, then apply its `world_matrix` once. Applying both the structural parent matrix and that world matrix would transform the node twice. Hidden and locked nodes remain in the snapshot. Hidden nodes are omitted from drawing, while a locked visible node remains drawable. Use the Part 4 graph API for structural edits and managed asset endpoints for authorized downloads. Re-fetch the runtime after updating a canonical scene. The core 3D snapshot does not perform the separate collision, overlap or clearance checks supplied by Part 2.

## Verification

From `insho-backend`, with the dedicated PostgreSQL test database configured:

```sh
./.venv/bin/python -m pytest -q tests/test_spatial_runtime.py tests/test_spatial_engine.py
./.venv/bin/python -m pytest -q -k 'not test_editor_real_browser'
./.venv/bin/python -m alembic check
```

The targeted run passed 21 tests. The full backend run passed 130 tests. Tests compare repeated serialized responses byte for byte, check canonical data and revisions after compilation, and exercise geometry, bounds, graph ordering, inheritance, corrupted input and authenticated tenant isolation. The current database schema remained at `d37b196ac241`. No frontend or database changes were required.

For a 2D plan and 3D runtime compiled at the same scene revision, see [the Phase 9 Part 6 synchronization contract](spatial-synchronization.md).
