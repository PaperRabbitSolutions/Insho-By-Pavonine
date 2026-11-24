# Phase 10 Part 3 — Materials, BOM, and costing

Part 3 uses Phase 9 object and asset references, Part 1 production records, items and material assignments, and Part 2 takeoffs and technical specifications. Catalogue definitions belong to an organization. A material assignment belongs to one production record and links that plan's material to its catalogue identity.

## Materials and assemblies

A material definition has a stable ID and versioned name, category, description, specification, finish, unit, status, rate, currency, and allowance. Supplier and source references are supplied as text. Each revision records its author and timestamp. Read an older definition with `?version=1`. Set status to `archived` to retire a definition. Existing material assignments can retain the link and show that it is unavailable for a new estimate.

Use the existing record material write endpoints to specify `definition_id` and an optional `overrides` object. Overrides can set rates, allowance, specification, finish, and source references for that production plan. Set `overrides` to `null` to clear them. A rate override keeps its original pricing unit. If the catalogue pricing unit changes, enter a rate for that unit before using the override in an estimate. Existing text-only material assignments remain readable and can be linked later.

An assembly is a Part 1 production item with `kind: "assembly"`. Link nested assemblies and components through `parent_item_id`. Update that link through the item PUT route. The backend rejects cycles, non-assembly parents and parents from another production record. Item measurements hold component quantities and units. Each item keeps its own specification and material assignment. Object Library definitions can serve as reusable spatial component references. Use one child item per distinct material requirement.

## Generate or revise a BOM

Each BOM belongs to a scene-scoped production record and inherits its project and optional event. Use `POST /boms` on that record to create the first revision, refresh selected takeoffs, add or adjust a manual line, or remove a line from the next revision.

Generated lines require a valid, latest and current Part 2 calculation plus a current approved structured technical sheet that identifies the material. Select the sheet explicitly when the item has multiple eligible sheets. A component can use a takeoff from itself or an ancestor assembly. When the sheet pins takeoffs, use one of those revisions for the generated line. The system converts compatible units into the material's pricing unit. Use an explicit `consumption` rule to map across unit families, such as 0.25 kg of finish per 1 m² of takeoff. The material allowance applies after conversion. Any allowance already applied by Part 2 remains part of the takeoff and is included in the trace. Discrete material counts round up.

A generated line stores the component hierarchy, quantity and scene revision, technical specification and approval, material definition and assignment versions, source references, and cost inputs. Missing mapping, unverified sources, incompatible units, and unavailable materials produce issue codes and a null total. A rate can be explicitly zero. An absent rate keeps a valid required quantity visible and leaves the line unpriced.

Each request identifies the current production record version, scene revision, and latest BOM revision. Use `expected_bom_version: 0` when creating the first BOM. Changes append a full revision with stable line keys and new line IDs. An unchanged generated request returns the current revision. Repeating a logical line in one request produces a conflict. Use latest-revision line IDs to edit or remove lines. Unchanged lines, including manually entered values, carry forward. Replacing a manually adjusted takeoff requires `replace_manual: true` on the corresponding generation request.

Owners and admins can approve the latest revision when all lines are current and priced. A later revision starts unapproved. Earlier approvals and figures remain readable. Source changes are reflected on reads through stale reasons such as `quantity_changed`, `specification_changed`, `component_changed`, `material_changed`, and `event_currency_changed`. An event currency change marks affected BOM and cost rollups stale while retaining their historic amounts in the original currency. For a different currency, use a new plan and price it explicitly. Refresh the affected Part 2 source and submit a new revision to recalculate. Independently entered costs need human review when they have no recorded dependencies.

## Cost rules

Manual lines accept `estimated`, `quoted`, or `actual` as their basis with an explicit source reference, quantity, unit, and optional rate. Categories include material, component, assembly, fabrication, labour, equipment, finishing, and other. Approval is a separate attribute of the whole BOM revision. A quote or actual cost does not change an earlier estimate.

BOM currency is an explicit uppercase three-letter code. It must match the linked event currency when one is configured. Each BOM series keeps its original currency and accounting precision. Currency precision is configurable from zero to four decimal places and defaults to two. Material units include supported metric and imperial lengths, square and cubic units, weight, and `each`. Explicit manual time lines also accept `h` and `day`. Quantities use six decimal places and rates support up to six decimal places. Conversions and financial calculations use Decimal.

1. Round quantity × unit rate to the BOM currency scale.
2. Apply the supplied discount percentage to the rounded base and round the discount.
3. Apply the supplied tax percentage to the discounted base and round the tax.
4. Add tax to the discounted base.

Amounts round half up. The default tax and discount are both zero. The formula and all adjustments are retained on the line. Incompatible currencies leave a line unpriced. The original rate and its currency remain visible in `unit_rate` and `unit_rate_currency`. Project totals group by currency, precision, and cost basis. A stale or unpriced line leaves the corresponding total null and keeps the priced subtotal visible. If a scene has multiple alternative records, use `record_id` filters to select the plan for rollup. The project response also reports the latest approved BOM per record separately.

## API examples

Record paths below are relative to `/api/v1/production/scenes/{scene_id}/records/{record_id}`.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/production/organizations/{organization_id}/materials` | Create a material definition. |
| `GET /api/v1/production/organizations/{organization_id}/materials` | List current definitions, optionally filtered by status. |
| `GET` or `PUT /api/v1/production/organizations/{organization_id}/materials/{definition_id}` | Read current or historical definitions, or create a revision with the current `expected_version`. |
| `POST /materials`, `PUT /materials/{material_id}`, `GET /materials/{material_id}` | Manage the record's material assignments and see effective values. |
| `POST /items`, `PUT /items/{item_id}`, `GET /items/{item_id}/breakdown` | Manage components and assemblies and read the ordered subtree. |
| `POST /boms`, `GET /boms`, `GET /boms/{bom_id}` | Create or revise a BOM, list history, or read line calculations and freshness. |
| `POST /boms/{bom_id}/approve` | Approve the latest BOM with the current record version, scene revision, and BOM version. |
| `GET /api/v1/production/projects/{project_id}/cost-summary` | Read project or event costs. Accepts `event_id` and repeated `record_id` filters. |

Example `POST /boms` request for a draft.

```json
{
  "expected_record_version": 8,
  "expected_bom_version": 0,
  "scene_revision": 3,
  "currency": "INR",
  "changes": [
    {
      "type": "generate",
      "quantity_id": "87f1a9bd-14f2-4938-96fa-049a94964e57",
      "specification_id": "2afe3e14-38dc-4f2c-8fe5-c61e54aee921",
      "discount_percent": "10",
      "tax_percent": "5"
    },
    {
      "type": "manual",
      "name": "Fabrication labour",
      "category": "labour",
      "basis": "quoted",
      "quantity": "2.5",
      "unit": "h",
      "unit_rate": "18.125",
      "source_reference": "Workshop quote 16"
    }
  ]
}
```

For a 32 m² takeoff with 10% material allowance and a 2.50 INR rate, the generated line includes the following result.

```json
{
  "origin": "generated",
  "basis": "estimated",
  "quantity": "35.200000",
  "unit": "m2",
  "unit_rate_currency": "INR",
  "total": "83.16",
  "cost": {"base": "88.00", "discount": "8.80", "tax": "3.96", "total": "83.16"},
  "stale": false,
  "issues": []
}
```

## Migration and Phase 12

Alembic revision `7af04e25b1c9` follows Part 2 revision `6c28f1a4d705`. It adds material catalogue definitions and revisions, BOM revisions and lines, and optional catalogue links on existing material assignments. The upgrade retains current production data. Downgrade removes the Part 3 tables and assignment links.

Phase 12 can consume catalogue versions and effective assignments, assembly breakdowns, BOM line issues and stale reasons, and separated current and approved project costs. Use the current revision fields returned by each endpoint for writes. Part 4 consumes the BOM and cost history for budgets and sourcing. [Budget and procurement guide](production-budget-procurement.md)
