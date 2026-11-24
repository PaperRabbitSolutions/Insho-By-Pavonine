# Phase 9 Part 3 — 2D spatial editor

The native JavaScript and SVG workspace is the historical Part 3 reference client. The backend contracts below remain available for Phase 12. See [the completion audit](spatial-completion.md) for the current integration flow.

## Open the workspace

Start the existing FastAPI application and open `http://127.0.0.1:8000/workspace`. Sign in with an existing INSHO account, choose an organization and project, and open a spatial scene. A designer can also create a scene by entering measured width, length and height and choosing an optional event. Project members can open scenes with view access. Direct links use `/workspace/projects/{project_id}/spatial/{scene_id}`.

The checkout contained FastAPI, PostgreSQL, pytest, and the prior spatial APIs. It had no frontend package, UI component library, browser routes, design tokens, or canvas implementation to reuse. The workspace is served by FastAPI on the API origin. It uses native JavaScript modules and an interactive SVG viewport. SVG provides precise rendered paths and interactive handles without a runtime dependency or a frontend build step.

## Editing and measurement

- Drag the viewport with Pan or Space, use the wheel to zoom around the cursor, and use the footer to reset zoom, center, or fit the scene, visible content or selection.
- Choose Object or Zone and a category. Click to use the category's editable initial dimensions, drag to draw a measured rectangle, or use **Add at scene center** from the keyboard. Placement, layer visibility and scene grid settings are saved.
- Click an object or select it in Layers. Shift adds or removes items, and Alt cycles objects sharing the same point. Drag to move the selection, use arrows to nudge, drag a square handle to resize a planar rectangle about its center, or drag the round handle to rotate. Hold Shift to suppress snapping for handle edits. The inspector provides authored dimensions, parent-local coordinates, rotation, zone and parent selection, visibility, appearance, material references and custom properties.
- Grid and object anchor snapping use world meters. The preview uses Part 2 anchors, distance tolerance and tie rules. Placement and movement confirm the final anchor with Part 2 before saving. Resize snaps the measured edge length to grid increments. Alignment and equal spacing use Part 2 proposals and convert the resulting world offsets to each object's parent coordinates.
- The ruler measures planar point distance. The inspector shows authored dimensions and projected bounds. Select two objects to request their minimum geometric clearance. Configure a clearance target in the scene inspector. Scene, assigned-zone and overlap findings appear in Geometry checks and can locate affected shapes.
- The Plan reference reads the current event Phase 8 Plan artifact and falls back to the project Plan when available. Existing managed assets can be browsed and attached to objects. Plan concepts and asset links are references for the designer.

## Persistence and integration

`app/web/spatial/` separates navigation and sign-in, viewport rendering, presentation math, input handling, the inspector, and action history. The camera maps right-handed, Z-up world meters to screen pixels, inverting the screen Y axis. The snapshot contains geometry computed by Part 2 and the unit factors from the Python conversion utilities. The editor works with the Part 1 records and IDs. Drawing and camera state are separate from scene records.

- `GET /api/v1/organizations/mine` lists only the signed-in user's memberships.
- `GET /api/v1/spatial/scenes/{id}/editor` reads a consistent scene revision with canonical zones, objects, references and relationship edges, together with derived footprints, world matrices and anchors.
- `PUT /api/v1/spatial/scenes/{id}/editor/changes/{action_id}` applies up to 500 ordered operations against `base_revision`. It reuses the canonical scene service and audit log. Any operation failure rolls back the action. The existing scene lock serializes writes, and a recorded receipt makes the same action ID and payload safe to retry after a lost response. The response includes the saved canonical snapshot.
- Part 2's validation, clearance, alignment and snap endpoints remain the geometry entry points. Snap accepts optional excluded roots, visibility filtering and a switch for geometry anchors. Original callers retain the default behavior.

A pointer gesture previews in the browser and queues one action on completion. Changes autosave after a short delay. Failed calculations and writes retain the current edit for retry in the open tab. The editor prompts on leaving with unsaved edits and can sign in again with the same account after expiry. A stale revision returns a conflict, leaving the user to discard the pending action and reload the latest saved scene. Undo and redo run as atomic writes with a 60-action history per open editor. Deletion undo restores object IDs and supported asset and relationship links through the same authorized service. Existing parent and assignment restrictions still apply.

No schema migration, frontend build tool, or new runtime dependency is required. The test harness uses Node and an installed Chrome for integration testing.

## Tests and scope

`node --test tests/browser/geometry.test.mjs` runs the isolated JavaScript geometry and history tests. To include the real Chrome workflow in the Python suite, set `INSHO_CHROME` to a Chrome executable and run `python -m pytest -q` from the backend with the dedicated PostgreSQL test database configured. The browser workflow runs the production FastAPI application with independent database sessions. Test setup seeds an isolated account/project; browser actions call the real API. The Phase 9 Part 3 run passed 107 pytest tests, including the Chrome workflow, and four isolated JavaScript tests.

The viewport bounds grid work, culls off-screen shapes and reuses unchanged SVG nodes across frames. In the local Chrome test, the editor loaded 242 objects and one zone, then rendered 17 shapes after zooming in. The bulk seed and snapshot round trip took 1.20 seconds. Most of the eight measured zoom updates settled within 33–34 ms across two animation frames. Scene snapshots carry the authorized scene as a whole. Undo history is local to the open editor. The editor offers direct handles for measured planar rectangles; custom and tilted shapes use the inspector for authored dimensions and transforms. Part 2 still reports unsupported ellipse and tilted non-box calculations. This editor provides geometric findings; it does not determine regulatory compliance.

## Implementation map and Part 4 handoff

| Area | Files |
| --- | --- |
| Workspace and editor modules | `app/web/index.html`, `app/web/spatial/` |
| Atomic scene actions and projected snapshots | `app/services/spatial_editor.py` |
| Integrated routes and canonical services | `app/main.py`, `app/api/v1/organizations.py`, `app/api/v1/spatial.py`, `app/services/spatial.py` |
| Part 2 integration refinements | `app/schemas/spatial_geometry.py`, `app/services/spatial_geometry.py`, `app/spatial/geometry2d.py`, `app/spatial/validation.py` |
| Tests | `tests/test_spatial_editor.py`, `tests/browser/` |

Part 4 can build on the editor snapshot and atomic action contract. The workspace already renders parent-child transforms, preserves object identities across edits and restores supported relationship edges and asset links during undo. A full object catalog, dedicated relationship authoring, and live collaboration are outside this editor's scope.
