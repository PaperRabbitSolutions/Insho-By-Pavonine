# Phase 9 Part 8 spatial validation

The validation report evaluates a canonical scene at one revision. It uses the Part 2 geometry engine, Part 4 scene graph, Part 5 compiler, Part 6 synchronized projections, and Part 7 object library. The service authorizes the scene through the existing project and, when associated, event checks. A shared scene lock keeps canonical edits out of the run; catalog and asset metadata can change independently of scene revisions.

## Report API

Send an authenticated `POST /api/v1/spatial/scenes/{scene_id}/validation-report`. An empty body runs the configured checks. Organization members with read access can generate reports. An unauthenticated request returns `401`, an inaccessible scene returns `404`, and an invalid request or saved policy returns `422`. A completed request returns `200` even if the scene has blocking findings. The editor continues to use the Part 2 `POST /api/v1/spatial/scenes/{scene_id}/validate` contract.

The report includes `run_id`, `schema_version`, project, event and scene IDs, `scene_revision`, `checked_at` in UTC, `duration_ms`, `configuration_fingerprint`, `findings_fingerprint`, summary counts for the whole run, executed rule descriptors, and the finding page. The findings fingerprint covers the ordered findings before filtering and pagination. `severity` and `category` filters can be combined. `offset` defaults to zero and `limit` defaults to 100 with a maximum of 500. `filtered_total` counts matching findings before pagination.

Each finding has a stable ID, rule and code, category, severity, blocking status, title, explanation, resolution, typed scene entities with names, related zone IDs, optional world bounds and anchor, optional measurements, and diagnostic metadata. The location encloses the associated measured entities, or uses the world origin when that is the only usable coordinate. A pair's anchor can be used to center a view. It is an approximate display location rather than a collision contact point.

| Status | Meaning |
| --- | --- |
| `valid` | All enabled checks completed and produced zero findings. |
| `valid_with_findings` | All enabled checks completed with warnings or information. |
| `incomplete` | At least one enabled check could not fully evaluate its inputs. |
| `invalid` | At least one blocking error was found. Check `complete` and the individual rule flags for coverage. |

Errors are blocking. Warnings describe issues that require review and information describes advisory conditions. The status and totals always apply to the full run, including findings outside the current page or filter. A disabled rule is absent from `rules_executed`.

## Rules

| Rule ID | Category | Default severity | Checks |
| --- | --- | --- | --- |
| `geometry` | `geometry` | Error | Dimensions, shape and footprint validity, unsupported or degenerate geometry. A zone with no dimensions or footprint produces a warning. |
| `transforms` | `transform` | Error | Finite local position, rotation and scale, supported coordinates, and valid composed world frames. |
| `hierarchy` | `hierarchy` | Error | Missing or cyclic parent chains, invalid grouping, ordering, layer and zone references, inherited state resolution, and invalid relationship endpoints. Legal lock and visibility settings are respected. |
| `venue_boundary` | `containment` | Error | World geometry vs the canonical rectangular venue, including floor and ceiling. Missing bounds produce an incomplete warning. |
| `zone_containment` | `containment` | Warning | Object outlines vs assigned zones and child zones vs parent zones. Objects inherit the nearest object's or group's zone, unless they have an explicit zone. |
| `zone_overlap` | `collision` | Warning | Overlaps between configured exclusive zone pairs. Edge contact is allowed. |
| `collision` | `collision` | Warning | Physical overlaps using transformed object footprints with height or 3D box intersection. Opt-in contact findings default to information. |
| `clearance` | `clearance` | Warning | Explicit object-to-object and object-to-zone separation requirements. |
| `consistency` | `consistency` | Warning | Synchronized plan and runtime agreement in bounds, references and shared state. Mismatches in generated data and client revisions ahead of the scene are errors. Stale supplied revisions are warnings. |
| `object_library` | `library` | Error | Definition scope, version pins, catalog defaults for current pins, and catalog and instance asset links. Archived definitions used by existing instances produce information. |

A rule severity override applies to all findings in that rule. Availability findings set the corresponding rule's `complete` flag to false. Invalid shapes may also leave measurement rules incomplete for affected objects. Legal overlaps and disabled rules do not reduce coverage.

## Policy configuration

Settings are merged in this order.

1. Built-in defaults.
2. The project's `constraints.spatial_validation`.
3. The scene's `metadata.spatial_validation`.
4. The report request's `config`.

Use the existing project and scene create or update APIs to save policies. Rule options merge individually by rule ID and collision options merge individually. Lists replace the previous layer, so an empty list clears an inherited list. Omitted values and individual null options inherit. The stored `spatial_validation` key takes an object. Unrecognized options, malformed IDs, duplicate clearance IDs, invalid severities, non-finite or nonpositive thresholds and self-pairs return validation errors. Rules start enabled. No clearance thresholds or exclusive pairs are configured by default.

This request uses a case-sensitive category selector for fixtures, keeps a measured access zone clear by 0.9 m, raises the severity of clearance findings, and returns the first page of clearance findings.

```json
{
  "plan_revision": 17,
  "runtime_revision": 17,
  "category": "clearance",
  "limit": 50,
  "config": {
    "rules": {"clearance": {"severity": "error"}},
    "collision": {
      "include_hidden": false,
      "ignore_ancestor_pairs": true,
      "report_touching": false,
      "allowed_pairs": [
        {"first_id": "e14b26d9-2255-4aa2-812c-2f6ee2cba9b1", "second_id": "aeabda6f-a18c-4b1d-883d-f8158347266d"}
      ]
    },
    "clearances": [
      {
        "id": "service-access",
        "subject": {"category": "fixture"},
        "against": {"zone_footprint_id": "5f572e13-eef4-4a12-8a77-e178a5fb8c03"},
        "minimum_m": 0.9
      }
    ]
  }
}
```

A failing clearance finding could have these fields.

```json
{
  "rule_id": "clearance",
  "code": "clearance_below_minimum",
  "category": "clearance",
  "severity": "error",
  "blocking": true,
  "measurement": {"measured": 0.5, "expected": 0.9, "shortfall": 0.4, "unit": "m"},
  "zone_ids": ["5f572e13-eef4-4a12-8a77-e178a5fb8c03"],
  "metadata": {"requirement_id": "service-access", "method": "2D distance to zone footprint"}
}
```

## Geometry and scope

- Coordinates are right-handed and Z-up, with the floor plan on XY. The authored venue spans 0 to width, depth, and height. The world bounds of measured geometry prove whether it extends beyond that envelope. The report distinguishes partial and fully outside placement when the intersection calculation supports it. `exceeds` means the envelope violation is known while the exact intersection classification is unavailable.
- Zone membership uses full XY outlines. The optional `require_zone_assignment` checks all physical objects. A zone with no measurable footprint cannot establish containment. Use `exclusive_zone_pairs` for zones that are deliberately mutually exclusive.
- Collision defaults to effectively visible physical objects. Geometry-free groups and objects with `properties.collision_policy` set to `"ignore"` are excluded. Ancestor and descendant objects are excluded by default. An `allows_overlap` relationship or configured `allowed_pairs` permits overlap in either direction. Locking does not exclude an object. Contact within the geometry tolerance is allowed unless the rule is configured to report it.
- Object clearance measures nearest surface distance using XY footprint and elevation separation for upright shapes, or convex surface distance for tilted boxes. Both selectors may include `object_id`, `zone_id`, and `category`. Combined fields all have to match; `zone_id` selects objects assigned to that zone. Two directions of a pair collapse to one finding per requirement. Explicit clearance rules apply to selected objects regardless of collision permissions or visibility. Missing explicit IDs produce an incomplete finding. Category selectors may currently match zero objects.
- For zone clearance, `against.zone_footprint_id` measures the minimum planar distance to the entire access or exclusion area. Entry into that area, including contact, measures zero clearance. Elevation is ignored for this mode. Spacing is compared using a base linear tolerance of `1e-6 m` plus `1e-12` of the magnitude used by the geometry engine. Measurements use meters, square meters, or cubic meters, regardless of the scene's display unit. Representation freshness uses revision numbers. The checks evaluate authored design requirements. They make no determination of regulatory compliance.
- An exact ellipse projection from a nonuniformly scaled circle, a tilted circular solid, or a tilted concave prism may be unavailable under the existing geometry contract. An unsupported candidate pair or projection produces an explicit incomplete result; bounds can still conclusively rule out distant pairs. Some tilted box intersections have a classification without an exact overlap volume. Archived or older catalog pins preserve authored instance geometry. Historical catalog defaults are unavailable, so only the current pin's defaults are checked against the library. Asset checks use database records and scope, without fetching binaries.

## Execution and Phase 12 integration

The report uses the shared graph resolver and compiles the plan and runtime once. Projected footprints, bounds, and exact-measurement objects are reused within the run. Collision and object clearance use an X-axis sweep and Y/Z bounds to discard distant pairs before exact work. Zone distance uses planar bounds. Definitions and asset links are fetched in batches. Dense scenes can still produce quadratic candidates and report volume. Filters and paging limit returned findings only.

Run reports are computed on demand and are not stored. No schema migration is needed. For unchanged canonical data, catalog and asset metadata, and equivalent inputs, findings are ordered by severity, rule ID, code, typed entity IDs, and finding ID. The finding ID uses scene, rule, code, normalized entity references, and an issue qualifier such as the clearance requirement ID. Changing severity or a measured distance leaves the ID stable as long as the same violation remains. Each run has its own timestamp, duration, and run ID.

Phase 12 can display the whole-run summary, use filtered pages for findings, and use typed `entities` and `zone_ids` to select scene nodes. Use `location` bounds and anchor to navigate, as available. Check `blocking` and `complete` together before treating a report as ready for a workflow. Compare `scene_revision` with the current canonical revision before using a page or highlight. Request a new report after a canonical edit or a project policy, catalog, or asset change. Compare the `configuration_fingerprint` when fetching additional pages. Pass the displayed plan and runtime revisions to surface stale inputs. Refresh both representations from `GET /api/v1/spatial/scenes/{scene_id}/synchronization` and submit the new revision numbers to revalidate.

## Adding a rule

Add a stable ID and any typed configuration to `app/schemas/spatial_validation.py`. Implement the check with the existing compiler or geometry utilities and register it in `RULES` in `app/services/spatial_validation.py`. Pass canonical entity references to `run.finding` with a stable issue code, resolution, and qualifier for distinct checks on the same entity or pair. Mark missing prerequisites incomplete and use the shared context for geometry and scope. Add focused tests for outcomes, unavailable data, stable finding IDs and ordering, and any policy options.
