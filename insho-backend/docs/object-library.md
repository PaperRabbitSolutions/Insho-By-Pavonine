# Phase 9 Part 7 — Asset and Object Library

## Definitions and instances

Phase 6 manages file uploads, storage, access, previews and lifecycle. Phase 9 already stores placed objects, layers, relationships and asset links in its canonical scene. The object library adds reusable definitions and links to existing Phase 6 asset IDs. It stores no files or provider URLs.

An object definition has a stable UUID and a scope, name, open category and object type, tags, description, measured dimensions, default transform, local geometry, appearance, material references, metadata and asset links. Categories and object types are open text fields. Tags normalize to unique lowercase values. Optional metadata must contain finite JSON and each field is limited to 64 KiB.

An instance is an ordinary `SpatialObject`. Its canonical authored geometry, dimensions, pose, appearance and material values are initialized from the source and the placement overrides. The instance also stores `library_definition_id` and `library_definition_version`. Catalog description, search tags and catalog metadata stay on the definition. Managed asset references are attached through the existing scene asset service. Copies of the same definition can therefore use independent transforms, dimensions, local metadata, visibility, hierarchy and layers.

## Geometry and compatibility

Definitions use the existing right-handed, Z-up coordinate system. X is width, Y is depth or length, and Z is height. All inputs use meters and right-hand-rule degrees. The default transform uses `T * Rz * Ry * Rx * S`. Parentless positions are relative to the scene. Parent object or group transforms apply through the shared graph and transform engine. The configured layer and assigned zone affect scene organization and inherited state.

All three measured dimensions are required. The library uses the canonical six-decimal storage precision, positive scales and the existing numeric range. The geometry field accepts these shapes:

| Type | Parameters and mapping |
| --- | --- |
| `box`, `rectangle`, `rect` | Centered box using authored width, depth and height. Box is the default shape when geometry is omitted. |
| `circle` | Optional `radius_m`, defaulting to half the width. It must fit both authored ground dimensions. Part 5 produces a cylinder with the authored height. |
| `polygon` | Three to 512 local, ordered `{x_m, y_m}` vertices. The outline must be simple and fit the authored dimensions. Part 5 produces a centered prism with the authored height. |
| `line`, `line_segment` | Optional local start and end. Defaults use the authored width. The endpoints must be distinct and fit the authored dimensions. |
| `point` | Local origin. Authored instance dimensions are still required. |

Shared Part 2 geometry validation, Part 5 meshing and Part 6 projection determine compatibility. Unsupported or invalid default geometry is rejected. A shape valid in 3D can carry a warning when its exact 2D outline is unavailable. The stored catalog flag `plan_compatible` describes the definition's default pose by itself. Placement evaluates the actual world pose, so a rotated or scaled parent can change compatibility. Geometry outside the scene bounds produces a placement warning. Use Part 2 for overlap and zone checks.

## Scope, references and lifecycle

Definitions can be shared within an organization, confined to one workspace, or confined to one project. Project-scoped definitions inherit the project's workspace. Scope is fixed once a definition is created. An organization's members can read its authorized catalog. Owner, admin and designer roles can create, modify, archive, duplicate and place definitions. Placement also requires write access to the target scene and a compatible definition scope.

A definition can use a Phase 6 asset whose scope covers every scene allowed by the definition. Organization-wide definitions take organization assets. A workspace or project definition can also use assets from its own workspace, and a project definition can use assets from its own project. Event-local assets are unsuitable for this reusable catalog. References use the existing `model`, `preview`, `material`, `texture` and `reference` roles. A thumbnail can be linked as a preview.

Asset availability reports whether an asset record is active and still compatible with the definition's scope. Soft deletion or scope changes can mark a reference unavailable and block placement until it is resolved. Phase 6's authorized download and preview routes check the actual stored object. Catalog and scene references protect a file from permanent cleanup while a reference remains. Existing geometry and scene objects are independent of an asset's availability. External model files remain asset references, and the runtime renders the authored canonical geometry.

## Versions and overrides

Definitions start at version 1. Updates and archive actions accept `expected_version` and return `409` if another writer has advanced it. Archive retains the same definition ID, keeps existing scene instances usable, and stops new placement. An authorized editor can restore a definition by updating its status to `active`. Duplication creates an independent active definition at version 1 and reuses the same asset IDs.

Placement requires the displayed `definition_version` and scene `base_revision`. It validates the definition, available asset references, target scope, geometry, and optional overrides. The scene service applies canonical parent, zone and layer rules, including inherited locks. Omitting `layer_id` uses the scene default and explicit null leaves the instance unassigned. The instance, its asset links and its scene revisions commit in one transaction.

An override can specify a name, dimensions, individual transform components, appearance, material references, instance metadata, parent, zone, layer and initial visibility. Omitted transform components retain the definition defaults. A dimension override must preserve a valid shape. A placement edit leaves the definition unchanged. Subsequent canonical edits validate geometry changes on linked objects. A metadata-only properties patch preserves the existing shape. Group duplication and editor restoration from the scene audit preserve provenance.

Definition versions are optimistic counters. The placed object's canonical values are its retained snapshot. The library does not expose a separate history of complete past definitions or apply changes automatically to existing instances.

## API contract

Catalog paths have the prefix `/api/v1/object-library/organizations/{organization_id}`. The bearer session and existing organization, workspace, project and scene checks apply. Cross-tenant and inaccessible definition or scene IDs return `404`. Read-only members receive `403` for writes.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/definitions` | Paginated summaries. |
| GET | `/facets` | Category and tag values and counts under the same filters. |
| POST | `/definitions` | Create a measured definition. |
| GET | `/definitions/{id}` | Retrieve details, available asset references and structured spatial validation. |
| PATCH | `/definitions/{id}` | Change fields or replace asset links with `expected_version`. |
| DELETE | `/definitions/{id}?expected_version=N` | Archive and return the retained definition. |
| POST | `/definitions/{id}/duplicate` | Create a copy with `expected_version` and an optional name. |

Search accepts case-insensitive literal `q` over name and description, `category`, `object_type`, repeated `tags` that must all match, `geometry_type`, `plan_compatible`, `asset_availability`, `min_width_m`, `max_width_m`, `scope`, and `status`. Default status is active. A `project_id` context includes the organization, its workspace and that project. A workspace-only context includes organization and workspace definitions. PostgreSQL performs tenant filtering and stable ordering by lowercase name and UUID. Use `offset` and `limit` up to 100. Pages report `has_more`. Facets use the same offset and limit per list and sort values alphabetically.

Place a definition with `POST /api/v1/spatial/scenes/{scene_id}/objects/from-library/{definition_id}`. Send `definition_version`, `base_revision` and any authored overrides. A successful response includes the canonical object, committed scene revision, and validation. A stale version, archived definition, unavailable asset at placement, or locked target returns `409`. Invalid shapes and inputs return `422`. Geometry and asset diagnostics include a code, severity, field, message and relevant definition or asset ID.

## Phase 12

Browse the catalog using the current project context. Show definition validation and asset availability in the inspector. Send a placement request using the current definition version and scene revision. Then fetch the paired Part 6 snapshot and match the new node by its canonical ID. Scene Graph, Part 5 runtime and both sides of Part 6 expose the same definition ID and pinned version. Edit the placed object through the existing canonical scene write APIs.

## Persistence and verification

Migration `eb4c51ad9027` adds the definition and asset link tables, scope and tag indexes, and nullable indexed provenance on existing scene objects. Foreign keys retain definition identity and protect referenced records from deletion. Ordinary scene objects retain null provenance.

The full backend command `./.venv/bin/python -m pytest -q -k 'not test_editor_real_browser'` passed 145 tests, including eight catalog cases. The migration preserved a preexisting scene record in a temporary PostgreSQL database and passed `alembic check`. The tests use real database rows and Phase 6 file upload and download. No frontend implementation or new runtime dependency was introduced.
