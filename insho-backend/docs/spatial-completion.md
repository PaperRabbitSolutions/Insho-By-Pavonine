# Phase 9 completion audit

**Verdict — Phase 9 complete with limitations.** The canonical scene, geometry, graph, synchronized representations, object library, validation, and design recommendation workflow are verified together. The Phase 12 contract uses those backend services and their existing revision model.

## Audit method and results

The audit followed the actual database models and migrations through authorization, mutation services, geometry adapters, compilers, and the published OpenAPI operations. It reviewed the relevant regression tests, exercised the existing browser entry point, and ran a new end-to-end scenario. File and contract review provided change verification in this workspace because the supplied checkout has no Git metadata.

| Part | Before Part 10 | Hardening and evidence | Final status |
| --- | --- | --- | --- |
| 1. Canonical architecture | Complete with Limitations | Verified project/event ownership, scene IDs, revision writes, same-scene foreign keys, and persistence. Invalid labels and nonrepresentable extension data now fail input validation before a write. The migration round-trip retained pre-existing authored scene object values. | Verified Complete |
| 2. Deterministic geometry | Complete with Limitations | Fixed asymmetric edge alignment, polygon precision far from the origin, segment endpoint tolerance, and grid overflow. Legacy geometry accepts independent valid branches when another transform is corrupt. Geometry tests cover area, snapping, SAT, containment, and clearance. Exact geometry support remains shape-dependent. | Complete with Limitations |
| 3. Editor foundation | Complete with Limitations | Verified atomic writes, rollback, idempotent action receipts, local/world transforms, undo restoration, and the existing Chrome workflow. The earlier native JavaScript and SVG workspace was frontend work introduced in Part 3. It is retained as a working compatibility client. Its editor snapshot and action response remain legacy response shapes. | Complete with Limitations |
| 4. Graph and objects | Complete with Limitations | Verified groups, ordering, default-layer inheritance, explicit null and preserved membership, effective visibility and locks, and deletion safeguards. The PostgreSQL checks exercise the actual `d37b196ac241` triggers. Very deep hierarchies return a flat-graph fallback from the legacy nested route. | Verified Complete |
| 5. 3D compilation | Complete with Limitations | Verified stable node IDs, world composition, winding, nested objects, mesh bounds, and structured failure diagnostics. Geometry that fails compilation is excluded from the footprint handoff. The compiler supports the authored shape contract and references external model assets. | Complete with Limitations |
| 6. Synchronization | Complete with Limitations | Verified mutations and stale versions against both views. A failed runtime shape can no longer remain drawable in plan output. Unsupported exact outlines still preserve the node and may remain drawable in 3D. | Complete with Limitations |
| 7. Object Library | Complete with Limitations | Verified scoped discovery, asset links, version pins, archive behavior, and independent instances. Corrupt stored transforms now receive structured catalog errors. Canonical attachment shares an asset read lock through commit. | Complete with Limitations |
| 8. Validation | Complete with Limitations | Verified configured collisions, boundary, containment, clearance, library and consistency checks with stable finding IDs and read-only reports. Geometry precision and paired-compilation fixes flow through the existing rules. Tilted non-box measurements can be incomplete. | Complete with Limitations |
| 9. Phase 8 integration | Complete with Limitations | Verified five module adapters, partial/failed runs, scoped source provenance, deterministic catalog resolution, warning approval, stale-state rejection, rollback, and duplicate retries. A project-wide artifact cannot use event-only references. The integration supports measured zone additions and catalog placements. | Complete with Limitations |

## Architecture and end-to-end verification

`spatial_scenes` and its related zone, object, layer, relationship and asset-link rows are the authoritative design. The graph reads and mutates those records with the same revision log. Authored numbers are meters and degrees with decimal storage at six places. The scene is right-handed and Z-up, with XY on the floor and positive rotation about the right-hand axes. Object pivots and local geometry remain authored separately from the world transform. Composition uses `parent_world * child_local`.

The pure geometry package operates on the resolved world data. One synchronized compilation produces both views with matching typed IDs and a canonical revision. Binary media stays in the Phase 6 storage layer, and the catalog remains independent of its placed instances. Part 8 produces read-only reports. Part 9 preserves Phase 8 artifact provenance, stages an editor-approved action through the same mutation services, runs Part 8, and saves an atomic receipt with the canonical change.

The cross-part test starts with an authorized event and measured scene, exercises event-specific input provenance, creates an existing zone and layered object, discovers a matching catalog definition with a managed asset link, and stages both a Plan review and an Elements placement. The placement commits, the earlier Plan review becomes stale, both views carry the placed object's catalog, zone and layer identities, and the current report validates without findings.

## API and authorization

The runtime route and OpenAPI audit verified **50 unique authenticated operations** across spatial, integration and object library routes. The two historical editor routes return legacy response shapes. Current graph, compiler, synchronization, library, validation and recommendation endpoints have explicit response models. Geometry inputs and metadata receive validation before persistence.

| Workflow | Contract |
| --- | --- |
| Load scenes and revisions | `GET /api/v1/spatial/projects/{project_id}/scenes`, `GET /api/v1/spatial/scenes/{id}`, `GET /api/v1/spatial/scenes/{id}/revisions` |
| Scene structure | `GET /api/v1/spatial/scenes/{id}/graph` and `/graph/nodes/{type}/{id}`. Use the flat graph for very deep scenes. |
| Commit changes | `PATCH /api/v1/spatial/scenes/{id}/graph` for structural operations or `PUT /api/v1/spatial/scenes/{id}/editor/changes/{change_id}` for authored edits, with `base_revision`. |
| Measure and edit geometry | `GET /api/v1/spatial/scenes/{id}/objects/{object_id}/geometry` and `POST` to the scene's `/snap`, `/align` or `/clearance`. |
| Retrieve views | `GET /api/v1/spatial/scenes/{id}/synchronization` with optional plan and runtime revisions. `/runtime-3d` provides the standalone compiled view. |
| Find and place catalog objects | `GET /api/v1/object-library/organizations/{org_id}/definitions` with scope and search filters, then `POST /api/v1/spatial/scenes/{id}/objects/from-library/{definition_id}` with scene and definition versions. |
| Validate | `POST /api/v1/spatial/scenes/{id}/validation-report` for status, counts, findings, coverage and filters. The historical editor also uses the measurement-oriented `/validate` route. |
| Review design intelligence | `GET /api/v1/spatial/scenes/{id}/design-integration` and `/recommendations`. Recommendation `/validate`, `/apply`, and `/reject` are POST routes. |

Bearer authentication protects every operation in this inventory. Project and linked event access govern scene reads. Editor roles govern writes. Scenes, catalog scope and asset references are checked before mutation. Structural writes and recommendation application lock the canonical scene row. Database constraints and migration triggers protect default layers, parent cycles and scene deletion. Tests exercised unauthorized reads/writes, scoped asset and definition selection, lock failures, the editor retry receipt, concurrent graph writes, and concurrent recommendation application.

Clients should handle `401` for missing authentication, `403` for insufficient edit permission, `404` for unavailable resources, `409` for locks, conflicts or a stale revision, and `422` for invalid input or unavailable requested geometry. Historical conflict details may be text. Newer review and validation routes expose codes and findings. A completed report can return findings that block a spatial action.

## Phase 12 handoff

Load a synchronized pair and use `(type, id)` to associate graph rows, plan outlines and runtime meshes. Draw a node when its corresponding view reports `renderable`. Respect effective visibility and locks. Send canonical authored fields and the current `base_revision`; after a write, reload the pair and use the returned revision. A single action can allocate several revisions. A stale edit or review must be refreshed before retry. The same editor action ID or applied review ID is safe to retry according to its contract.

Use the validation report for severity counts, completeness, and finding locations. Check scene revision when displaying findings. Use the scoped design context and recommendation detail for provenance, candidates, preview actions, and approvals. A saved recommendation can become stale after a scene, source, asset or catalog change. Successful application returns the canonical entity and revision for navigation.

Detailed contracts and sample payloads are in [canonical architecture](spatial-architecture.md), [geometry](spatial-engine.md), [graph](spatial-graph.md), [runtime](spatial-runtime-3d.md), [synchronization](spatial-synchronization.md), [catalog](object-library.md), [validation](spatial-validation.md), and [design integration](spatial-design-integration.md).

## Database and remaining limits

The local development schema is at `02e73da81bf4` with no Alembic drift. A disposable PostgreSQL database verified the fresh chain, upgrade over existing Part 1 data, downgrade and re-upgrade, and retention of the seeded legacy geometry and visibility. Offline SQL generation also passed. Part 10 required no schema change; deployment migration status remains environment-specific.

Supported geometry includes boxes, circles, polygons, segments and points, with different runtime and exact measurement coverage. Nonuniform circles and some tilted or concave shapes can lack an exact plan outline or collision result. External model binaries remain Phase 6 references. Dense intersections can generate quadratic candidate and finding counts. Scene synchronization returns a full snapshot, and report pages limit returned findings rather than compute work. Geometry and provider fixtures were local, with mocked provider calls and no production load or cloud storage verification. The historical workspace is a compatibility client; the Phase 12 product UI and its dedicated tests are separate work.

## Verification

The command results below are from the backend directory. Each listed command exited 0.

| Verification | Passed | Failed | Skipped or deselected |
| --- | ---: | ---: | --- |
| Focused spatial, Phase 8 and tenancy suite | 128 | 0 | 1 browser test deselected |
| Full pytest regression suite with Chrome | 193 | 0 | 0 |
| Existing Node geometry and history tests | 4 | 0 | 0 |
| Python compilation, OpenAPI route inventory, Alembic migration and drift checks | Completed | 0 | 0 |

```sh
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m pytest -q tests/test_phase9_spatial.py tests/test_spatial_*.py tests/test_object_library.py tests/test_phase8_design_intelligence.py tests/test_authorization_and_tenant_isolation.py -k 'not test_editor_real_browser'
INSHO_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m pytest -q
node --test tests/browser/geometry.test.mjs
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m compileall -q app migrations scripts tests
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m alembic upgrade head --sql > /private/tmp/insho_phase9_schema.sql
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python /private/tmp/insho_part4_migration_check.py
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m alembic current
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m alembic check
PYTHONPYCACHEPREFIX=/private/tmp/insho-pycache ./.venv/bin/python -m scripts.benchmark_spatial
```

The initial focused run had one failure. Its new end-to-end fixture exceeded the asset storage key column's 32-character limit. The corrected fixture uses ordinary UUID hex keys. Existing and new regressions are green. The repository has no configured linter, formatter or static type-check command.

## Performance

The benchmark uses FastAPI's in-process HTTP test client, a disposable local PostgreSQL database and the production endpoint serialization. Each latency is the median of three requests after a warm-up. Fixtures include eight zones, a 12-group chain, rotated boxes, circles and concave polygon prisms. The sparse layouts use a 3 m grid. The dense layout uses 0.2 m spacing and generated 3,328 findings at 100 objects. These timings characterize the local fixtures.

| Scene workload | Synchronized read | Validation report | Synchronized payload |
| --- | ---: | ---: | ---: |
| 25 sparse objects | 22.3 ms | 20.0 ms | 142 kB |
| 100 sparse objects | 64.6 ms | 52.8 ms | 430 kB |
| 250 sparse objects | 160.5 ms | 101.2 ms | 1.02 MB |
| 500 sparse objects | 283.1 ms | 230.9 ms | 1.99 MB |
| 100 dense objects | 63.8 ms | 2,086.4 ms | 430 kB |

The 500-object requests peaked at 30.6 MiB of traced Python allocations for synchronization and 10.6 MiB for validation. A 50-item page in a 600-definition catalog took 13.5 ms. Profiling showed repeated rectangle-vertex construction during dense intersections. Reusing vertices on each immutable rectangle reduced the dense validation median from 2,306.3 ms to 2,086.4 ms across the separate local runs. There is no persisted geometry cache. Existing broad-phase pruning avoids exact work for distant pairs, while dense cases still require substantial pair evaluation.
