# Phase 9 Part 4 — Scene Graph and Object System

## Architecture

Part 1 supplied project and event scoped scenes, zone and object parents, zone assignment, object metadata and categories, typed relationship edges, asset links, and a revision log. Part 2 supplied local and world transform math and geometric validation. Part 3 supplied an authorized backend snapshot and atomic editor writes. Part 4 extends those records and services. Graph reads project the persisted scene at a single revision.

- A zone is a row in `spatial_zones`. It can belong to the scene or another zone.
- An object is a measured row in `spatial_objects`. An object or group parent takes precedence in the tree. Without one, the object's assigned zone determines its tree location. Zone assignments remain independent of object transforms.
- A group is a `spatial_objects` row with `node_kind: "group"`. It has a canonical transform, metadata, and optional zone and layer assignments. It has no physical dimensions. The geometry engine composes its transform for descendants and excludes the group from geometry, overlap and snapping calculations.
- A layer is a row in `spatial_layers`. It belongs to a scene and has its own name, metadata, order, visibility and lock. Objects and groups can each belong to one layer. Layer membership leaves the tree unchanged. A scene can designate one default layer.
- Typed relationships and managed asset links continue to use the original spatial tables. The graph includes references to assets; existing asset authorization still applies when attaching a link.

## Structural edits

Grouping takes existing siblings in their current structural order and inserts an identity transform group at the position of the first member. This preserves the member IDs, authored transforms, measured dimensions, zone and layer memberships, metadata, and links. Groups can nest or have an object parent. An empty group can be created with an explicit zone, parent and transform.

Use `reparent` to set or remove a parent, add a member to an existing group, or move between groups. Zones can only be children of zones. For an object or group, selecting a zone sets its zone and clears its object parent. Selecting an object or group keeps the existing zone assignment. A null parent moves a node to the scene root and clears any object zone assignment. To remove a member while keeping its zone assignment, use its zone as the new parent or its group's own object parent, as appropriate.

Reparenting and ungrouping preserve world transforms through Part 2 matrix composition, inversion, and decomposition into the stored translation, rotation and scale. The service verifies the reconstructed world matrix against the shared Part 2 tolerance after rounding to the database precision. A destination that would require shear, reflection, or values beyond the supported precision is rejected atomically. The original object and zone PATCH APIs still interpret explicit parent and transform fields as authored local values.

Ungroup lifts direct children to the group's former object parent and preserves each child's own zone. Children that remain in the same sibling list take the group's slot. Members, asset links, and internal typed edges retain their identities. Detach any links owned by the group itself before ungrouping. Deleting a nonempty group returns a conflict. Duplicating a group copies up to 500 nodes with new IDs and the same world geometry, copies managed asset links and relationships internal to the subtree, and appends the duplicate to the source sibling list. Edges to outside nodes stay attached to the original subtree.

## Default layer

Scenes start with no default layer. Use `set_default_layer` with a layer ID from the current scene, or pass null to clear it. Setting or clearing a default leaves existing memberships in place. For a new measured object, omitting `layer_id` uses the scene default. An explicit layer ID chooses another layer, and explicit null leaves the object unassigned. Groups use their explicit assignment and preserve the layers of their members. The existing editor transaction restores the previous assignment from the audit history when recreating a deleted object without a layer field.

An active default must be cleared or replaced before its layer can be deleted. Choosing or clearing a default respects layer locks. If the default is locked, attempts to create objects on that layer return `409`. Callers can explicitly place new objects in another unlocked layer or leave them unassigned.

## Ordering and state rules

Siblings, layers, and layer members each have their own persisted order. The service uses sparse integer ranks and changes one rank when a gap is available. If a gap closes, it renumbers only the affected list while preserving its intended order. Existing rows begin at rank zero and use kind, creation time and ID to break ties. New nodes and new memberships append. Moving an object within a layer can cross parent boundaries without changing its tree order. A layer with assigned members must be emptied before deletion.

Local visibility for objects, groups and zones comes from their existing status. An active node is locally visible. Layers keep a separate local boolean. Effective visibility requires the local state, all applicable parents, assigned zones and layers to be visible. Hiding an ancestor or layer leaves each member's local state intact. Hiding an archived node is a no-op. Showing it requires restoring its status first.

Effective lock is the union of local, ancestor, assigned-zone and assigned-layer locks. Both graph and preexisting spatial mutation routes enforce it. Locked nodes cannot be edited, deleted, moved, reordered, assigned, have links changed, or receive new members. Transform and structural edits check affected branches for locked descendants before writing. A configured editor may change a node's local lock explicitly, including under an inherited lock. The inherited lock stays effective until its source is unlocked. Order compaction may renumber locked peers while preserving their relative order. Project and event deletion also respect scene graph locks. Authorized editors can explicitly unlock the source of the lock before deleting the container.

## HTTP contract

All paths below use `/api/v1/spatial/scenes/{scene_id}`. Membership is required to read; owner, admin and designer roles can write. The existing scene check also verifies event, project, workspace and organization scope.

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/graph` | Scene and project IDs, revision, ordered root references, normalized nodes with parent and child references, ordered layers with members, `default_layer_id`, and typed relationships. Nodes include status, local and effective state, both ranks, zone and layer IDs, metadata, asset references and timestamps. |
| GET | `/graph/nodes/{type}/{id}` | A node and its parent, ordered children, nearest-first ancestors, depth-first descendants and ordered siblings. Types are `zone`, `object` and `group`. |
| PATCH | `/graph` | Apply up to 100 operations in request order. Supply the current `base_revision`. Returns the committed revision and affected or created IDs in operation order. A failed operation rolls back the entire batch. |

A reference is `{"type":"object","id":"<uuid>"}`. State and ordering operations also accept `layer` references. Create operations can accept a client supplied UUID for reference in later operations in the same batch.

| Operation kind | Required payload and behavior |
| --- | --- |
| `create_group` | `group` with a name and optional category, transform, parent, zone, layer, appearance, properties, tags and status. Optional `members` are sibling references. |
| `update_group` | `id` and one or more of `name`, `category`, `transform`, `properties`. |
| `delete_group`, `ungroup`, `duplicate_group` | Group `id`. Duplicate returns the new group ID followed by its copied subtree IDs. |
| `reparent` | `node`, `parent` reference or null. |
| `reorder` | `node`, `before` reference or null to append. Default scope is `siblings`, which also orders the layer list. Set `scope: "layer_members"` to order members of one layer. |
| `create_layer` | `name`, optional `metadata` and `visible`. |
| `update_layer`, `delete_layer` | Layer `id`. Update accepts `name` and `metadata`. |
| `set_default_layer` | `layer_id` from this scene, or null to clear the default. |
| `assign_layer` | `nodes`, `layer_id` UUID or null. |
| `set_visibility` | `nodes`, `visible` boolean. Omit `visible` to toggle each local value. |
| `set_locked` | `nodes`, `locked` boolean. |

Errors follow the existing API conventions. Missing or out-of-scope references return `404`. Unauthenticated calls return `401` and a read-only member's write returns `403`. Invalid operation shapes and target kinds return `422`. Stale revisions, locks, cycles, unsafe deletion and unrepresentable transforms return `409`. After a conflict, fetch the latest revision and make a fresh decision before submitting a new action.

## Persistence and Phase 12 integration

Migration `c481a7d02f64` adds the layer table, object kind, ranks, and lock fields. Database checks distinguish measured objects from dimensionless groups and enforce valid rank fields. Composite foreign keys enforce same-scene parents, zones and layers, and indexes support traversal and ordering. All mutations serialize under the scene row lock and use the existing scene revision log. Migration `d37b196ac241` adds a partial unique index for the per-scene default and triggers that reject cyclic object or zone parents, including direct database writes. It checks the preexisting graph during upgrade. A scene deletion trigger protects locks during project and event cascades. Downgrade is restricted once graph data would be lost.

A Phase 12 client can use one graph response to build the tree, layer panel and search index, and use typed canonical IDs to join to geometry from the existing APIs. For drag and drop, submit a reparent and reorder together against the displayed revision. Use effective state to control interaction, and use local state when displaying or changing toggles. Read the graph after a write and use its revision to coordinate with the existing editor snapshot. The existing frontend was outside the Part 4 change.

## Verification commands

From `insho-backend`, with the isolated PostgreSQL test database configured:

```sh
./.venv/bin/python -m pytest -q -k 'not test_editor_real_browser'
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m alembic check
```

The backend run passed 121 tests. The existing Chrome workflow was excluded. The backend tests cover graph projection and traversal, geometry preservation, group copying, ordering, effective states and lock enforcement, rollback, simultaneous writers, reload, tenancy, default layer behavior, and PostgreSQL cycle and deletion guards. A separate PostgreSQL database was used to upgrade through Part 1, seed a legacy object, and verify that the Part 4 migration preserved its geometry and status. The scratch database also passed an eligible downgrade and re-upgrade.
