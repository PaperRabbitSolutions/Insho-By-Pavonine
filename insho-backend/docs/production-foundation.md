# Phase 10 Part 1 — Production foundation

Production records belong to a Phase 9 scene and inherit its project and optional event. A record contains assemblies, components, materials, technical specifications, and measurements. An assembly can contain other assemblies or components. Each item can reference one material, so an assembly can represent multiple materials through its components.

Use `/api/v1/production/scenes/{scene_id}/records` to create and list records. `GET /{record_id}` returns the full record with source and measurement freshness. The list reports scene freshness. Nested `/items`, `/materials`, `/items/{item_id}/specifications`, and `/items/{item_id}/measurements/{kind}` routes manage the draft. Child mutations return the updated `record_version` for the next write. Draft mutations require `expected_version`. Deletion takes it as a query parameter. A conflict returns HTTP 409 and leaves the record intact. Only owners and admins can approve. Approved source records remain read-only after source changes. Create a new draft with `supersedes_id` pointing to the approved record in the same scene. The successor starts with an empty package for explicit authoring and review.

## Measurements and provenance

| Kind | Accepted units | Canonical unit |
| --- | --- | --- |
| Length, width, height | `m`, `cm`, `mm`, `ft`, `in` | `m` |
| Area | `m2`, `cm2`, `mm2`, `ft2`, `in2` | `m2` |
| Volume | `m3`, `cm3`, `mm3`, `ft3`, `in3` | `m3` |
| Count | `each` | `each` |
| Weight | `kg`, `g`, `lb` | `kg` |

Amounts use `NUMERIC(24,6)` with half-up rounding. Measurements must be positive and representable. A count must be a non-negative integer. Length maps to Phase 9 depth/Y, width to X, and height to Z.

- `spatial_geometry` uses a scene, linked object, or linked zone at the requested scene revision. Object and zone dimensions use the world bounds. Plan area uses the Phase 9 footprint projection. Volume comes from scene bounds or transformed box geometry. Other shape volumes, weight, and count need a supplied value.
- `object_library` uses the current version of a linked active definition for its authored dimensions. Items linked to placed library objects also retain the placement’s pinned definition version.
- `user_entered` accepts an explicit value and unit. Replacing a manual value with a different source requires `replace_manual: true`.
- `technical_specification` accepts an explicitly transcribed value and unit linked to a versioned specification on the item.
- `ai_suggestion` accepts an explicit value and unit tied to a reviewed Phase 9 recommendation and its exact review ID. Approval remains a separate human action.

Phase 9 scene revisions conservatively flag geometry measurements and the record for review after any scene edit. Changed catalog definitions, specifications, AI reviews, and missing spatial sources are also visible in record detail. Source deletion clears the live item link and keeps the original ID and captured values. A changed source assignment can be addressed by creating a replacement draft item with the new link. Use `POST /{record_id}/review-sources` with the current `scene_revision` and `expected_version` after resolving stale measurements and missing links. This acknowledges the revision without changing values. Then use `POST /{record_id}/approve` with the reviewed revision and current record version.

## Production lifecycle

Part 1 approval freezes the source record. The operational lifecycle has its own version and history on that same record. A record with no workflow history starts at `draft`, version `0`, including records with an existing Part 1 approval.

`draft` → `design_approved` → `technical_review` → `production_ready` → `in_fabrication` → `on_site` → `installed` → `inspected` → `live` → `dismantling` → `archived`.

Owners and admins record transitions in order. Supply `evidence_reference` at Design Approved, Inspected, and Archived. During the first three states, editors can save an ordered sequence of tasks with stable caller-provided UUIDs. Each task can reference a production item and supplier and carry notes, a duration in whole minutes, and a start and end window with time zones. Dependencies must reference earlier tasks. Types cover fabrication, rigging, electrical, installation, transport, storage, floral delivery and storage, testing, commissioning, inspection, and dismantling. With complete durations, `critical_path` and `critical_path_minutes` report the longest dependency chain. Window and resource constraints are recorded for review. Schedule gaps appear in `warnings`.

Production Ready requires the latest current, priced, approved BOM with estimated requirements. Every work item needs estimated BOQ coverage and a current approved Technical Sheet with dimensions, technical parameters, fabrication notes, or installation requirements. Assemblies used solely for grouping can rely on their children. Generated lines pin the sheets they used. Tasks with item or vendor links require those links to resolve. `readiness_basis` records the BOM, sheet, source-record, and scene revisions reviewed. `readiness_blockers` identifies the information to resolve.

A change to the reviewed basis sets `readiness_stale`. Before fabrication, return to `technical_review`, revise the package and sequence as needed, and transition through Production Ready again. Fabrication requires the package to match the recorded review. Later states can record site activity while still surfacing stale sources. Evidence references are authored references and record a human decision.

| Endpoint under `/api/v1/production/scenes/{scene_id}/records/{record_id}` | Contract |
| --- | --- |
| `GET /workflow` | Read the status, version, plan, readiness basis and blockers, warnings, critical path, and configured next states. Server-side role and readiness checks apply to the listed transitions. |
| `PUT /workflow/plan` | Replace the sequence using `expected_version` and `tasks`. A repeated equivalent plan at the current version returns that version. |
| `POST /workflow/status` | Record the next `status` with `expected_version` and optional `evidence_reference`. Returns `409` for a version conflict, blocked readiness or illegal transition and `422` for missing required evidence. |
| `GET /workflow/history` | Read workflow revisions newest first with `offset` and `limit`. |

Project and event deletion checks protect workflow, approval, and quotation history from cascading removal. Existing draft-only deletion still uses the normal authorization and scene-lock checks.

## Migrations and linked modules

Run `alembic upgrade head` to apply the production chain. Part 1 starts at `5b03a8d6e194` after Phase 9 revision `02e73da81bf4`. Part 5 revision `4b9d5a732e10` follows `8d31b6e2f4a7` and adds the workflow revision table and same-scene predecessor relationship. Existing records keep their values and approval states. Downgrading Part 5 discards only workflow revisions and predecessor links. Back up those records before downgrading an environment that uses them.

Part 2 adds versioned takeoffs, configurable allowances, and structured Technical Sheets. Part 3 uses those inputs for materials, BOMs, and costing. Part 4 connects the BOM to budgets, quotes, procurement, and value engineering. [Quantification guide](production-quantification.md) · [BOM and costing guide](production-bom-costing.md) · [Budget and procurement guide](production-budget-procurement.md)

Phase 11 Part 4 records installation, labour, and delays against an exact workflow record, revision, and task ID. Installation compares elapsed time with that revision's task duration or planned window. Start and completion lateness are calculated from the same authored window. [Learning Engine and Actuals Capture](../README.md#phase-11-part-4--learning-engine-and-actuals-capture)
