# Phase 10 Part 2 — Quantification and technical specifications

Part 2 uses Part 1 production records, items, materials, specifications, and draft/approval controls. Phase 9 supplies measured geometry, object placements, hierarchy transforms, scene revisions, and verified links to applied design recommendations. Quantity Analysis adds one append-only calculation table. Part 1 measurements remain available for authored or individually captured values.

## Quantity rules

| Method | Formula and source | Result unit |
| --- | --- | --- |
| `plan_area` | Area of the Phase 9 world XY footprint. A scene uses its width × depth. | `m2` |
| `surface_area` | Magnitude of the cross product of two transformed rectangular face edges. Choose `xy`, `xz`, or `yz`. | `m2` |
| `linear` | Pick a local X, Y, or Z extent and apply the world scale along that axis. Use `path` for the world distance between a canonical segment’s endpoints. | `m` |
| `solid_volume` | Local footprint area × authored height × absolute determinant of the world linear transform. Supports boxes, canonical polygon prisms and cylinders, and scene bounds. | `m3` |
| `object_count` | One unit for each explicitly selected physical placement. Archived objects and groups do not qualify. Hidden physical objects still count. | `each` |
| `led_surface` | Multiply the two perpendicular edges of the selected box face. Divide each edge by the specified pitch and verify an integral pixel grid within 0.000001 m. Supplied resolution must fit. If omitted, the same fit test validates the derived integer resolution. | `m2`, with pixel counts in outputs |
| `floral_stems` | One placed arrangement × its explicit `stems_per_arrangement` configuration. Supply a nonempty `recipe_reference`. | `each` |

For methods other than LED, an optional `allowance_percent` from 0 to 1000 adds `base × (1 + percent / 100)`. An explicit allowance on a count rounds up to the next whole unit. Length, area, and volume use the Phase 9 meter convention and Part 1’s six-place, half-up normalization. The calculation applies allowance before rounding the final value. LED pitch and authored technical dimensions accept `m`, `cm`, `mm`, `ft`, and `in`. Pitch retains its converted precision for checking resolution. Phase 9’s geometry operations use the canonical transforms and projection. Decimal arithmetic applies to metric conversions, allowances, counts, and persisted values.

Geometric results describe the modeled footprint, face, extent, or volume. They cannot establish stock consumption, structural behavior, fixture suitability, or material density. Precise curved surface area, fabric drape, arbitrary imported mesh volumes, electrical load, rigging, fire-safety, and structural derivation are outside these rules. Floral recipes remain explicit inputs.

## Quantity API

Record routes use `/api/v1/production/scenes/{scene_id}/records/{record_id}` as their prefix.

| Method and suffix | Behavior |
| --- | --- |
| `POST /quantities/calculate` | Calculate up to 100 explicitly configured rules against items in the draft record. |
| `GET /quantities` | List latest versions. Use `history=true` for the version history and `offset` / `limit` for pagination. |
| `GET /quantities/{quantity_id}` | Read a calculation version and its trace, including an older version. |
| `POST /quantities/{quantity_id}/recalculate` | Reuse the latest version’s rule configuration on the specified current scene revision. |
| `GET /api/v1/production/projects/{project_id}/quantities` | List lines across project records, optionally filtered by `event_id`, with history and pagination. Group by `record_id` when displaying separate production plans. |

Example body for `POST /quantities/calculate` using a linked stage object.

```json
{
  "expected_version": 4,
  "scene_revision": 7,
  "calculations": [{
    "item_id": "9e69b7ee-bc5d-48e7-8b2a-cebf0cfc89fd",
    "target": "object",
    "rule": {"method": "plan_area", "allowance_percent": "10"}
  }]
}
```

For a 64 m² footprint, the response includes this result.

```json
{
  "record_version": 5,
  "quantities": [{
    "method": "plan_area",
    "value": "70.400000",
    "unit": "m2",
    "outputs": {"base_value": "64.000000"},
    "version": 1,
    "engine_version": 1,
    "scene_revision": 7,
    "status": "calculated",
    "errors": [],
    "latest": true,
    "stale": false,
    "approved": false
  }]
}
```

The full result includes the IDs, timestamp, actor, formula, parameters, canonical geometry inputs, source, and outputs. Invalid request fields return HTTP 422. A configured calculation with insufficient or incompatible source geometry stores an `invalid` result, error details, and a null numeric value. A batch with a bad relationship or duplicate line fails atomically. Version conflicts return HTTP 409. Include the current `expected_version` and `scene_revision` for recalculation.

A line is unique within its record by source target and ID, method, face or axis where applicable, and calculation version. A repeat with the same source inputs, engine version, and configuration returns the existing result. Explicit changes or recalculation append a version. Records capture the scene revision at calculation time and test freshness against the current physical source. A parent scale change can affect a child’s geometry. Unrelated scene edits and translation alone leave a quantity current. An archived or deleted source becomes stale and its original ID stays in the history. Applied Phase 9 review receipts identify source design artifacts and elements when available.

Record approval requires current, valid latest calculations and freezes the record. The latest lines inherit its approval receipt. Phase 9 edits can subsequently flag those lines stale while retaining the approved values. Recalculating an approved plan uses a new draft record.

## Technical specification API

Use the existing `POST /items/{item_id}/specifications` and `PUT /items/{item_id}/specifications/{id}` routes with the optional `technical` object. The existing `name`, `content`, and `expected_version` fields still apply. The item supplies the material association. The specification captures the material and its description as they were at the time of recording. Add finish, fabrication notes, installation requirements, or labelled technical parameters explicitly. Technical parameter values are supplied by authors.

Example request for a geometry suggestion.

```json
{
  "expected_version": 5,
  "name": "Stage deck",
  "content": "Deck layout and finish",
  "technical": {
    "source": "spatial_geometry",
    "target": "object",
    "scene_revision": 7,
    "finish": "Matte laminate",
    "fabrication_notes": "Seal panel edges.",
    "installation_requirements": "Follow the approved placement."
  }
}
```

The POST returns the specification ID and `record_version`. Fetch the sheet with `GET /items/{item_id}/specifications/{id}` or all sheets with `GET /items/{item_id}/specifications`. A geometry suggestion response includes these fields.

```json
{
  "version": 1,
  "status": "suggestion",
  "stale": false,
  "technical": {
    "source": {"type": "spatial_geometry", "target": "object", "scene_revision": 7},
    "dimensions": {
      "width": {"value": "8.000000", "unit": "m"},
      "length": {"value": "4.000000", "unit": "m"},
      "height": {"value": "1.000000", "unit": "m"}
    },
    "finish": "Matte laminate",
    "parameter_source": "user_entered"
  }
}
```

Use `source: "user_entered"` to supply a `dimensions` mapping with an explicit value and length unit for each measured axis. Geometry suggestions use Phase 9 world-bounds dimensions and require a live source with positive dimensions. Technical specifications also detect placement, finish, and material changes. A text-only Part 1 specification remains readable and retains its version.

An owner or admin can approve a current sheet with `POST /items/{item_id}/specifications/{id}/approve`, using `expected_version` and `scene_revision`. Approval captures actor and timestamp. An approved sheet can be revised on a draft record by creating a successor with `supersedes_id`. Both sheet versions remain readable. Approval here is a production workflow decision. Any engineering-sensitive information comes from the author and its appropriate external review.

## Database and Phase 12

Alembic revision `6c28f1a4d705` follows Part 1 revision `5b03a8d6e194`. It adds the quantity history and optional structured, approval, and predecessor fields to the existing specification table. Downgrading to Part 1 removes those additions.

Phase 12 can use the project or event read for Quantity Analysis, the record read for draft context, quantity detail for formula and history, and specification detail for Technical Sheets. Render `stale`, `status`, `errors`, and approval independently. Use the returned record version for subsequent mutations. Part 3 consumes current takeoffs and approved sheets for materials and costing. [Materials, BOM, and costing guide](production-bom-costing.md)

Part 5 adds optional `technical.quantity_ids` on specification writes. Select current calculated revisions belonging to the item or one of its parent assemblies. The Technical Sheet returns `technical.quantity_refs` with pinned IDs and versions, and its `technical.source` records the capture actor. If a pinned takeoff becomes stale, superseded or unavailable, the sheet reports `quantity_changed`. Recalculate the takeoff, create and approve a successor Technical Sheet, then revise the BOM with both new source IDs. Historical sheets remain readable. Sheets saved before this field was introduced retain their original source information.
