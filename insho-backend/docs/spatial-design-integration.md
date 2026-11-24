# Phase 9 Part 9 design intelligence integration

The integration reads saved Phase 8 outputs, derives spatial intent, and gives an editor a way to review a proposed addition against the canonical scene. Phase 8 remains the source of design artifacts and execution history. Canonical scene writes go through existing zone and catalog placement services. The bridge uses no provider calls.

## Sources and context

A scene inherits the latest artifact for each module in its event scope, with project-wide fallback when that event has no artifact for the module. A project-only scene reads project-wide artifacts. Freshness is checked against the original artifact's scope, upstream versions, project context, and available references. A GET refresh reads saved state, so it can see an artifact change without a scene revision change.

| Module | Output used by the spatial integration |
| --- | --- |
| Mood | Atmosphere, emotional direction, palette, materials, textures, styling, lighting and uncertainty. The authored confidence is retained for review. |
| Style | Design language, spatial character, visual hierarchy, composition, form, materials and conflicts. An exact overlap with curated tags can break ties among otherwise matching catalog records. |
| Plan | Conceptual zone names, types, purposes, dimensions and capacity provenance, adjacencies, flow, assumptions and constraints. Exact names map to existing active or hidden zones. A missing measured zone becomes a proposed addition with unresolved dimensions and position. |
| Vision | Structured text view plans, focal-point labels, consistency notes and limitations. An unambiguous exact name shared with a valid Elements record can surface that element's category. Reference metadata is limited to active assets in the artifact’s own project and event scope. A project-wide artifact accepts project-wide references, and an event artifact may also use references belonging to that event. |
| Elements | Element name, category, suggested style, materials, related zone, quantities and sources, descriptions, notes and authored confidence. Individual saved element edits and confirmations are honored. The original artifact keeps the unedited suggestions. |

Normalization reuses the existing Phase 8 output schemas. If a legacy artifact has invalid fields, supported independently valid fields remain visible with field-level diagnostics. The context reports each module as `available`, `partial`, `invalid`, `missing`, `stale`, `failed`, or `running`. The whole context can be `available`, `partial`, or `unavailable`. A later failed execution can be shown alongside a still-valid older artifact. The bridge uses the existing execution record with an event scope marker for new Phase 8 executions. Older unscoped failed runs cannot be assigned reliably to a particular event.

The context identifies artifact ID, version, source, execution ID, event scope, upstream lineage, and accessible assets. Unavailable references contribute a count and mark the source stale. Vision confidence and physical dimensions are unavailable from its view descriptions. Plan and Elements dimensions and quantities are retained as source intent. Plan zone creation requires editor-confirmed dimensions, and object placement uses the chosen definition's canonical dimensions. Each placement creates one instance.

## Recommendations and catalog resolution

Plan zone and Elements placement recommendations carry a deterministic ID, a source artifact and requirement path or element ID, structured intent, zone matches, candidate definitions, and missing prerequisites. Recommendations link to the original Phase 8 artifact for review. UUIDs are derived from the scene and the source artifact and requirement identity. They are stable across unchanged reads and designer scene edits. New artifact versions derive new recommendations.

A unique existing zone name resolves a Plan zone requirement. Ambiguity is shown as a list of matches. Plan adjacencies retain their textual relationship and any resolved scene zone IDs. For placement of an element linked to a named zone, the editor explicitly selects an existing scene zone. A named zone may be unresolved even when the catalog has a candidate.

The catalog search uses active organization, workspace and project definitions available to the scene. It considers exact normalized element name, category, or matching object type. Those same signals are ranked in that order. Matching style tags, normalized name, and definition ID break ties. The response includes up to five candidates, each with its reasons, dimensions, version and 2D compatibility. The discovery query considers at most 24 definitions per matching term and field. An editor can select another authorized matching definition outside the displayed list. The normal catalog placement service verifies its version, shape, transforms, and available assets.

Recommendation reads are derived as needed. Status describes whether an intent is `proposed`, `resolved`, `needs_resolution`, `ready_for_validation`, `validated`, `applied`, `rejected`, or `stale`. Filters work on the current derived state plus persisted reviews. A superseded recommendation with an existing review remains addressable through that review and its original source ID.

## Review and application

1. Read the scene's recommendations and retain the returned `scene_revision` and `context_version`.
2. Submit a typed action. A zone action requires explicit dimensions and XYZ position in meters. A placement action requires the catalog definition ID and version plus XYZ position. Catalog placement also accepts an existing target zone or layer.
3. The review service locks the scene revision and stages the canonical change in a PostgreSQL savepoint. Part 8 evaluates the staged scene, including the proposed zone's measured venue bounds. The preview change and its scene revisions are rolled back.
4. A successful preview returns a `review_id` and the full validation summary with up to 500 findings. A blocking or incomplete run returns `blocked` with findings and cannot be approved. The venue boundary remains blocking for these two actions. The geometry, transform, hierarchy, consistency, library, and zone containment checks run during review. Collision and clearance use the configured policy. Warnings require explicit acceptance when applying.
5. Applying the review is the editor's approval. The service rechecks the current scene, intelligence version, selected library version, active references, locks, and current Part 8 findings. It commits the canonical change, scene revisions, provenance and application receipt together. If the rules or findings changed, the editor must review another preview.
6. Retrying an applied `review_id` returns the same receipt and entity ID. Concurrent requests serialize on the scene lock. A failed transaction leaves the scene and review recoverable. Rejection saves a final decision and an optional reason. It leaves the canonical scene unchanged.

There are two supported action types, `create_zone` and `place_object`. Both are additions. The typed contract accepts neither existing-object edits nor destructive commands. Application history stays in the review table and existing canonical revision log. Applied zones carry provenance in `metadata.design_intelligence`. Applied objects carry it in `properties.instance_metadata.design_intelligence` and pin their normal library definition ID and version.

A stale review requires a fresh preview. A changed source artifact may also require selecting a new recommendation. Part 8's `findings_fingerprint` lets the application recheck all findings independently of page size, timing, and run ID. The pending review stores its action, fingerprints and counts. Full preview findings are returned on demand. Source artifacts, execution outputs, and original asset content use their existing storage.

## API and security

All paths below are relative to `/api/v1/spatial/scenes/{scene_id}/design-integration`. Use the existing bearer session. Reads require project and associated event access. Validate, apply and reject require scene edit permissions.

| Method | Path | Contract |
| --- | --- | --- |
| GET | Base path | Read the current normalized design context and per-module provenance and status. |
| GET | `/recommendations` | Derive recommendations and join their review state. Supports `source`, `kind`, `status`, `offset` and `limit`. |
| GET | `/recommendations/{recommendation_id}` | Read the scoped recommendation, candidate explanations, prerequisites, and saved review. |
| POST | `/recommendations/{recommendation_id}/validate` | Preview a measured action against the supplied revision and context version. |
| POST | `/recommendations/{recommendation_id}/apply` | Approve the saved `review_id`. Set `accept_warnings` to true after reviewing warnings. |
| POST | `/recommendations/{recommendation_id}/reject` | Save a rejection with the scene revision, context version and optional reason. |

A validation response with `blocked` uses HTTP 200 to return structured findings. HTTP 422 signals invalid or unresolved input. Scene, context, definition, or review conflicts return HTTP 409. Unauthenticated callers receive 401. Read-only members attempting an edit receive 403. Missing or out-of-scope scene, recommendation, zone, definition or layer references return 404. Scope checks apply to both recommendations and their underlying references.

## Example and Phase 12 contract

An Insho Plan result contains a Dining zone with a purpose and no dimensions. If the scene has no matching Dining zone, the recommendation is `proposed` with measured dimensions and position as prerequisites. A designer can review dimensions and submit them as follows. The sample revision and context version stand in for values from the recommendation list.

```json
{
  "base_revision": 17,
  "context_version": "05573753b2145859f4ce632014d604475500883ee78ae1c1f28ca73b2ccdf646",
  "action": {
    "kind": "create_zone",
    "dimensions": {"width_m": 8, "depth_m": 5, "height_m": 0.2},
    "transform": {"position": {"x": 10, "y": 7, "z": 0.1}}
  }
}
```

The preview shows Part 8 findings and an ID for the proposed entity. On approval, post the returned `review_id` to `/apply`. The receipt identifies the new zone and scene revision. Phase 12 can then reload the existing synchronization endpoint, navigate to the new zone by ID, and display the 2D and 3D representations at that revision. For catalog placements, candidate version and matching reasons help the editor select a definition. A Phase 12 findings view can use the Part 8 finding's typed entities and world bounds.

UI consumers can refresh the context and recommendations after a source update, compare `scene_revision` and `context_version` with a pending review, and show stale state from the API. They can display the exact saved action and preview summary on a pending review, revalidate for current findings, and render the returned application receipt or rejection reason. Use the existing synchronization and graph contracts for compilation and scene navigation.

## Persistence and verification

Migration `02e73da81bf4` creates `spatial_design_reviews`. It stores the current review and final receipt or rejection per scene and recommendation. Review calculation compiles one staged scene with Part 8. Source normalization loads project context once and batches artifacts, reference metadata, and catalog search. Individual actions are reviewed one at a time. Phase 8 AI provider execution retains its original retry and fallback behavior. Live provider generation is outside the verification of this deterministic bridge.
