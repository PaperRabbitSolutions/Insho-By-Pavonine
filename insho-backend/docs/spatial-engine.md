# Phase 9 Part 2: Spatial Engine and Deterministic Geometry

## Engine boundary

`app/spatial/` is a pure in-process math package. It has no database session, HTTP, provider, or frontend dependency. `app/services/spatial_geometry.py` is the adapter that resolves authorized persisted scene objects and parent transforms into pure geometry values. The API then presents calculated results; it does not implement another geometry algorithm.

No additional dependency or database migration is required. Derived geometry is calculated from canonical scene/object rows and is not stored, so transform or dimension edits cannot leave cached geometry stale. Object IDs are preserved by calculations. Alignment and snapping APIs return proposals with `persisted: false`; a later editor can apply a deliberate object update. Geometric duplication returns a new geometry value, while a persistence caller must create a new domain object identity.

## Coordinates, transform composition, and units

The Part 1 `right_handed_z_up` convention remains the only coordinate system: origin at the lower-left floor corner, +X right/east, +Y away/north, +Z upward. All positions, dimensions, footprints, distances, grid spacing, snap tolerances, and clearance thresholds use meters. Angles use degrees.

Object transforms use the column-vector matrix `T * Rz * Ry * Rx * S`: local scale first, then local X rotation, Y rotation, Z rotation, and translation. A child world matrix is `parent_world * child_local`; parentless transforms are already in scene coordinates. Matrix inversion supports nonsingular affine transforms, including shear introduced by composing nonuniform scale and rotation. Point transforms include translation; direction transforms do not. Normals use inverse-transpose transformation.

Object position is the local pivot and, for built-in box/rectangle geometry, the center of its authored dimensions. Dimensions extend equally in both directions from that pivot along local axes. Zone dimensions use the same center convention. Child transforms are parent-relative. Object-to-zone assignment is organizational; it does not implicitly make the zone the object's transform parent.

Numeric geometry calculations use Python double precision. The shared `Tolerance` policy defaults to `1e-6 m` absolute linear tolerance and `1e-12` relative tolerance. Area comparisons derive an area tolerance from the same policy. No geometry is rounded globally. Decimal unit conversion remains in `app/spatial_units.py` and is exact for the supported meter ratios; conversion never mutates canonical scene data. Linear conversions use the ratio of unit lengths, area conversions square that ratio, and volume conversions cube it.

## Supported geometry and validation

Pure 2D types are finite points, line segments, rotated rectangles, circles, and simple polygons. Rectangles expose both an oriented box and an axis-aligned bounding box. `AABB2`/`AABB3` provide bounds, centers, dimensions, corners, area/volume as appropriate. A transformed 3D box is represented by eight corners as a parallelepiped, so composed nonuniform scales and rotations are retained instead of being forced back into an inaccurate Euler box.

The scene API derives object geometry from the canonical object transform and dimensions. `properties.geometry.type` (or `properties.geometry_type`) may be `box`/`rectangle`, `circle`, `polygon`, `line`/`line_segment`, or `point`; the default is `box`. Custom polygon vertices and line endpoints use local `{x_m, y_m}` values around the object pivot and must fit inside its authored width/depth. A circle can specify `radius_m`, otherwise radius is half the authored width; it must fit the width and depth. All scale and parent transforms are applied. Circles remain exact only under uniform in-plane scale and orthogonal transformed axes; an affine ellipse is reported as unsupported rather than approximated.

Zone footprints are polygons in zone-local XY meters. A zone with no footprint uses its measured width/depth rectangle; an unmeasured zone has unknown containment. Polygon inputs require at least three finite, unique vertices, non-negligible area, and a simple non-self-intersecting boundary when evaluated by the engine. The schema rejects fewer than three points; the deterministic engine performs the geometric validity checks.

Rotated object footprints are transformed into world XY. Upright objects are checked with their exact 2D footprint plus vertical interval, so separate elevations do not collide. Tilted boxes use a 15-axis separating-axis test on transformed parallelepipeds. Tilted non-box collision and clearance are reported as unknown/unsupported instead of using a rectangular substitute. The scene boundary is the canonical axis-aligned box `[0,width] x [0,depth] x [0,height]`.

## Intersection, overlap, containment, and distance semantics

- Boundary points count as inside. Shapes that touch within tolerance count as intersecting, with classification `touching` and zero-area/zero-volume contact; they are not reported as a positive-area overlap.
- Results classify supported intersections as `none`, `touching`, `partial_overlap`, or `fully_contained`. Coincident or nested shapes classify as `fully_contained`.
- Rectangle/polygon intersection uses convex clipping; simple concave polygon intersection uses ear clipping followed by triangle clipping. Circle-circle intersection area is analytic. Circle-polygon area uses edge/sector integration. Contact points are returned only where the algorithm calculated them.
- Polygon and rectangle containment checks full edges, not only centers or vertices, so a segment/edge that exits a concave boundary is rejected. Circle containment uses the center and minimum distance to the polygon boundary.
- Object-to-object collision in upright scenes combines the 2D footprint intersection with overlap of vertical intervals. Tilted boxes use SAT. Overlap area is returned when the planar intersection algorithm computes it; overlap volume is exact for upright prisms and for axis-aligned 3D boxes, otherwise it is `null`.
- Minimum distance is between shape boundaries, not centers. Upright solids combine planar footprint distance and vertical gap; tilted boxes use closest points on convex surfaces. Clearance passes when measured distance plus tolerance meets the requested threshold. Clearance is geometric only and always reports `regulatory_assessment: false`.
- Scene containment is `inside`, `partial`, or `outside` and uses the complete footprint/solid. A center inside the venue does not make a crossing object contained.

Polygon area and centroid use a footprint-local origin for numerical stability. Area tolerance uses shape spans, and segment endpoint comparisons convert the linear meter tolerance to per-segment parameters. Legacy geometry reads hold the canonical revision while resolving transforms and can still measure independent branches when another transform is invalid. A request for an unresolved transform or unrepresentable grid coordinate returns `422`.

## Geometry editing calculations

`operations.py` provides pure move, rotate, resize, duplicate-value, center, bounds, corners, area, perimeter, bounding-radius, overlap, and clearance calculations. Circle resize rejects nonuniform scaling because that would create an ellipse, which is not a supported 2D type.

`snapping.py` calculates grid indices and nearest grid coordinates without generating a full grid. Half-step ties round away from the grid origin. Viewport point generation is bounded by an explicit `max_points`. Object anchors include center, shape vertices/cardinal circle points, and edge midpoints. Anchor snaps within the supplied world-meter tolerance take precedence; ties are deterministic by distance, target ID, then anchor kind. Grid snap is used when no anchor qualifies. Screen pixels are never accepted as world tolerances.

`alignment.py` returns proposed centers for left, right, horizontal/vertical center, top, bottom, and shared-center alignment. Edge alignment translates the measured footprint edge and preserves the offset between its centroid and bounds. Equal distribution sorts by AABB center along X or Y and spaces AABB extents evenly between the endpoints; it rejects a negative free gap. These calculations do not persist positions.

## Constraints and validation API

`POST /api/v1/spatial/scenes/{scene_id}/validate` checks every measurable object's scene boundary, assigned-zone footprint containment, object overlaps, and optional requested constraints. Supported constraint types are:

- `object_inside_scene`
- `object_inside_zone`
- `objects_do_not_overlap`
- `minimum_distance`
- `aligned` (object center along X, Y, or Z within a tolerance)
- `within_range` (the full transformed object bounds fit between supplied world-space min/max coordinates)

Each result includes ID/type, subject/related resource, `pass`/`fail`/`unknown`, optional severity, calculated value, expected condition, reason, and tolerance. Unsupported rules and unavailable geometry remain `unknown`; they are never silently marked as passing. Overlap findings are reported independently and do not automatically fail a scene because object contacts can be intentional. A scene overall status fails for invalid geometry, boundary/zone violations, or failed explicit constraints; it is unknown when required geometry or a requested rule is unsupported.

Other server routes are `GET /api/v1/spatial/scenes/{scene_id}/objects/{object_id}/geometry`, `POST /api/v1/spatial/scenes/{scene_id}/snap`, `POST /api/v1/spatial/scenes/{scene_id}/align`, and `POST /api/v1/spatial/scenes/{scene_id}/clearance`. They all resolve the scene through existing project authorization. Align and snap return proposals only; geometry and validation are read-only. No calculation invokes AI.

## Performance and complexity

World matrices are resolved once per scene query with memoization over in-memory parent rows, avoiding per-object database lookups. Scene overlap checks use an X-axis sweep and Y/Z AABB filtering, giving typical `O(n log n + k)` candidate work (`k` is the number of broad-phase candidates); dense scenes can still have `O(n^2)` candidates. Convex polygon clipping is quadratic in edge counts; concave polygons are triangulated and triangle pairs clipped, which is more expensive and intended for ordinary venue footprints rather than high-vertex GIS meshes. There is no persisted derived-geometry cache or spatial database index in Part 2.

## APIs and later editor integration

All geometry routes use the existing bearer auth and project membership/editor roles. Scene/object IDs are verified through the authorized scene; cross-tenant IDs remain hidden. The Part 3 editor should read canonical objects, call the object geometry/validation and calculation routes when it needs server-authoritative results, and submit deliberate canonical transform/dimension updates. Canvas pixels should be mapped to meters in the client presentation layer. The backend remains authoritative for stored measurements and validation; the same pure engine is callable directly by future editor services.

## Known limits

Part 2 does not include a 2D editor, renderer, AI placement, fabrication/BOQ, codes, safety certification, or regulatory rules. Exact 3D intersection and distance are implemented for box/parallelepiped solids; upright circles/polygons use exact planar footprint geometry plus a vertical interval. A tilted custom polygon/circle becomes an extruded non-box solid and is currently unknown for 3D overlap/clearance. Nonuniformly transformed circles are unsupported ellipses. The engine does not claim professional CAD/BIM precision or building-code compliance. No spatial objects or geometry results are synthesized from Phase 8 design artifacts.

See [the Phase 9 Part 5 runtime guide](spatial-runtime-3d.md) for compiled local meshes, world transforms and render bounds.
