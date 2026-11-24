"""Scene graph projections and atomic structural edits over canonical records."""

from collections import defaultdict, deque
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.spatial import SpatialLayer, SpatialObject, SpatialObjectAsset, SpatialObjectRelationship, SpatialZone
from app.schemas import spatial_graph as schema
from app.schemas.spatial import (
    SpatialAssetAttach, SpatialGroupCreate, SpatialObjectCreate, SpatialObjectUpdate, SpatialRelationshipCreate, SpatialTransform, SpatialZoneUpdate,
)
from app.services.spatial import (
    SpatialService, _check_object_parent, _check_zone_parent, _commit, _conflict, _flush, _get_scene, _missing, _object_response,
    _record_revision, _relationship_response, _row_state,
)
from app.services.spatial_geometry import _local_transform
from app.services.spatial_policy import (
    ORDER_STEP, StateResolver, append_order, default_layer, local_state, order_key, require_branch_unlocked, require_unlocked,
    scene_row, sibling_queries,
)
from app.spatial.math3d import IDENTITY, compose_transforms, decompose_transform, inverse_matrix, transform_matrix
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import GeometryError, Transform3, Vec3


_MODELS = {"object": SpatialObject, "group": SpatialObject, "zone": SpatialZone, "layer": SpatialLayer}


def _kind(row):
    if isinstance(row, SpatialObject):
        return row.node_kind
    return "zone" if isinstance(row, SpatialZone) else "layer"


def _key(row):
    return (type(row), row.id)


def _ref(row):
    return {"type": _kind(row), "id": row.id}


def _layer_member_key(row):
    return (row.layer_order_index is None, row.layer_order_index or 0, row.created_at, row.id.int)


def transform_parent_key(row):
    """Objects inherit object/group frames; zones inherit zone frames."""
    if isinstance(row, SpatialZone):
        return (SpatialZone, row.parent_zone_id) if row.parent_zone_id else None
    return (SpatialObject, row.parent_object_id) if row.parent_object_id else None


def _parent_key(row):
    key = transform_parent_key(row)
    if key is None and isinstance(row, SpatialObject) and row.zone_id:
        return (SpatialZone, row.zone_id)
    return key


def _entity(db, scene, ref):
    row = scene_row(db, _MODELS[ref.type], scene.id, ref.id)
    if _kind(row) != ref.type:
        _missing()
    return row


def _layer_payload(layer, members):
    return {
        "id": layer.id, "name": layer.name, "order_index": layer.order_index, "visible": layer.is_visible,
        "is_default": layer.is_default,
        "locked": layer.is_locked, "metadata": layer.metadata_json, "members": members,
        "created_at": layer.created_at, "updated_at": layer.updated_at,
    }


def _scene_rows(db, model, scene_id):
    return db.scalars(select(model).where(model.scene_id == scene_id)).all()


class GraphRead:
    def __init__(self, db, scene):
        self._load(scene, _scene_rows(db, SpatialLayer, scene.id), _scene_rows(db, SpatialZone, scene.id),
                   _scene_rows(db, SpatialObject, scene.id), _scene_rows(db, SpatialObjectAsset, scene.id))

    @classmethod
    def from_rows(cls, scene, *, layers, zones, objects, assets):
        graph = cls.__new__(cls)
        graph._load(scene, layers, zones, objects, assets)
        return graph

    def _load(self, scene, layers, zones, objects, assets):
        self.scene = scene
        self.layers = sorted(layers, key=order_key)
        self.nodes = sorted([*zones, *objects], key=order_key)
        self.by_key = {_key(row): row for row in [*self.nodes, *self.layers]}
        self.children = defaultdict(list)
        self.members = defaultdict(list)
        self.assets = defaultdict(list)
        for row in self.nodes:
            self.children[_parent_key(row)].append(row)
            if isinstance(row, SpatialObject) and row.layer_id is not None:
                self.members[row.layer_id].append(row)
        for link in sorted(assets, key=lambda row: (row.created_at, row.id)):
            self.assets[link.object_id].append({"id": link.id, "asset_id": link.asset_id, "role": link.role})
        self.state = StateResolver(lambda model, row_id: self.by_key[(model, row_id)])

    def parent(self, row):
        return self.by_key.get(_parent_key(row))

    def node(self, row, *, state=None):
        parent = self.parent(row)
        obj = isinstance(row, SpatialObject)
        if state is None:
            try:
                state = self.state.resolve(row)
            except KeyError:
                _conflict("The scene graph has a missing reference. Inspect the scene validation report.")
        return {
            **_ref(row), "name": row.name, "category": row.category,
            "parent": _ref(parent) if parent is not None else None,
            "children": [_ref(child) for child in self.children[_key(row)]],
            "order_index": row.order_index, "layer_id": row.layer_id if obj else None,
            "library_definition_id": row.library_definition_id if obj else None,
            "library_definition_version": row.library_definition_version if obj else None,
            "layer_order_index": row.layer_order_index if obj else None, "zone_id": row.zone_id if obj else None, "status": row.status,
            "visible": local_state(row).visible, "locked": row.is_locked,
            "effective_visible": state.visible, "effective_locked": state.locked,
            "metadata": row.properties if obj else row.metadata_json,
            "assets": self.assets[row.id] if obj else [],
            "created_at": row.created_at, "updated_at": row.updated_at,
        }

    def preorder(self):
        """Visit reachable nodes in canonical sibling order, with parents first."""
        pending, seen = list(reversed(self.children[None])), set()
        while pending:
            row = pending.pop()
            key = _key(row)
            if key in seen:
                continue
            seen.add(key)
            yield row
            pending.extend(reversed(self.children[key]))

    def layer_payloads(self):
        return [_layer_payload(layer, [_ref(row) for row in sorted(self.members[layer.id], key=_layer_member_key)])
                for layer in self.layers]

    def relations(self, row):
        node = self.node(row)
        ancestors, descendants = [], []
        parent = self.parent(row)
        while parent is not None:
            ancestors.append(_ref(parent))
            parent = self.parent(parent)
        remaining = list(reversed(self.children[_key(row)]))
        while remaining:
            child = remaining.pop()
            descendants.append(_ref(child))
            remaining.extend(reversed(self.children[_key(child)]))
        return {
            "scene_revision": self.scene.current_revision,
            "node": node, "parent": node["parent"], "children": node["children"],
            "ancestors": ancestors, "descendants": descendants,
            "siblings": [_ref(peer) for peer in self.children[_parent_key(row)] if _key(peer) != _key(row)],
        }


def graph(db, scene_id, user_id):
    scene = _get_scene(db, scene_id, user_id, lock="read")
    read = GraphRead(db, scene)
    relationships = sorted(_scene_rows(db, SpatialObjectRelationship, scene.id), key=lambda row: (row.created_at, row.id))
    return {
        "scene_id": scene.id, "project_id": scene.project_id, "scene_revision": scene.current_revision,
        "default_layer_id": next((layer.id for layer in read.layers if layer.is_default), None),
        "roots": [_ref(row) for row in read.children[None]],
        "nodes": [read.node(row) for row in read.nodes],
        "layers": read.layer_payloads(),
        "relationships": [_relationship_response(row) for row in relationships],
    }


def node_relations(db, scene_id, user_id, node_type, node_id):
    scene = _get_scene(db, scene_id, user_id, lock="read")
    read = GraphRead(db, scene)
    row = read.by_key.get((_MODELS[node_type], node_id))
    if row is None or _kind(row) != node_type:
        _missing()
    return read.relations(row)


def _scene_siblings(db, scene_id, key):
    if key and key[0] is SpatialObject:
        queries = sibling_queries(scene_id, object_parent=key[1])
    else:
        queries = sibling_queries(scene_id, zone_parent=key[1] if key else None)
    return sorted([row for query in queries for row in db.scalars(query)], key=order_key)


def _world(db, scene_id, model, parent_id):
    parent_field = "parent_zone_id" if model is SpatialZone else "parent_object_id"
    frames = []
    seen = set()
    while parent_id is not None:
        if parent_id in seen:
            _conflict("The scene graph contains a cycle.")
        seen.add(parent_id)
        row = scene_row(db, model, scene_id, parent_id)
        frames.append(transform_matrix(_local_transform(row)))
        parent_id = getattr(row, parent_field)
    result = IDENTITY
    for local in reversed(frames):
        result = compose_transforms(result, local)
    return result


def _preserving_patch(db, row, old_parent_id, new_parent_id):
    if old_parent_id == new_parent_id:
        return {}
    try:
        old_parent = _world(db, row.scene_id, type(row), old_parent_id)
        new_parent = _world(db, row.scene_id, type(row), new_parent_id)
        if old_parent == new_parent:
            return {}
        old_world = compose_transforms(old_parent, transform_matrix(_local_transform(row)))
        new_local = decompose_transform(compose_transforms(inverse_matrix(new_parent), old_world))

        def rounded(vector):
            return Vec3(*(round(getattr(vector, axis), 6) for axis in ("x", "y", "z")))

        stored = Transform3(rounded(new_local.position), rounded(new_local.rotation_deg), rounded(new_local.scale))
        recomposed = compose_transforms(new_parent, transform_matrix(stored))
        if any(not DEFAULT_TOLERANCE.equal(actual, expected)
               for actual_row, expected_row in zip(recomposed, old_world)
               for actual, expected in zip(actual_row, expected_row)):
            raise GeometryError("World transform cannot be preserved at canonical storage precision.")
        return {"transform": {
            name: {axis: Decimal(str(getattr(vector, axis))) for axis in ("x", "y", "z")}
            for name, vector in (("position", stored.position), ("rotation_deg", stored.rotation_deg), ("scale", stored.scale))
        }}
    except GeometryError as exc:
        raise HTTPException(409, detail=f"Cannot preserve world geometry: {exc}") from exc


class GraphWrite:
    def __init__(self, db, scene, user_id):
        self.db = db
        self.scene = scene
        self.user_id = user_id

    def entity(self, ref):
        return _entity(self.db, self.scene, ref)

    def entities(self, refs):
        rows = [self.entity(ref) for ref in refs]
        if len({_key(row) for row in rows}) != len(rows):
            raise HTTPException(422, detail="Each node must appear only once in an operation.")
        return rows

    def group(self, group_id):
        return self.entity(schema.NodeRef(type="group", id=group_id))

    def revision(self, operation, row, before=None, after=None):
        _record_revision(self.db, self.scene, self.user_id, operation, _kind(row), row.id, before, after)

    def update_object(self, row, **changes):
        SpatialService.update_object(self.db, row.id, SpatialObjectUpdate(**changes), self.user_id, commit=False)

    def _siblings(self, row):
        if isinstance(row, SpatialLayer):
            return sorted(_scene_rows(self.db, SpatialLayer, self.scene.id), key=order_key)
        return _scene_siblings(self.db, self.scene.id, _parent_key(row))

    def reorder(self, row, before, *, scope="siblings"):
        require_unlocked(self.db, row)
        if before is row:
            raise HTTPException(422, detail="A node cannot be placed before itself.")
        rank_field = "order_index"
        if scope == "layer_members":
            if not isinstance(row, SpatialObject) or row.layer_id is None:
                raise HTTPException(422, detail="Layer member ordering requires an assigned object or group.")
            if before is not None and (not isinstance(before, SpatialObject) or before.layer_id != row.layer_id):
                _conflict("Both nodes must belong to the same layer.")
            old = sorted(self.db.scalars(select(SpatialObject).where(
                SpatialObject.scene_id == self.scene.id, SpatialObject.layer_id == row.layer_id,
            )).all(), key=_layer_member_key)
            rank_field = "layer_order_index"
        else:
            old = self._siblings(row)
            if before is not None and before not in old:
                _conflict("Ordering is defined only within the same sibling or layer list.")
        ordered = [sibling for sibling in old if sibling is not row]
        position = ordered.index(before) if before is not None else len(ordered)
        ordered.insert(position, row)
        if ordered == old:
            return
        low = getattr(ordered[position - 1], rank_field) if position else -1
        high = getattr(ordered[position + 1], rank_field) if position + 1 < len(ordered) else None
        if high is None:
            ranks = [(row, low + ORDER_STEP)]
        elif high - low > 1:
            ranks = [(row, (low + high) // 2)]
        else:
            # Compaction preserves existing relative order and touches only this list.
            ranks = [(sibling, index * ORDER_STEP) for index, sibling in enumerate(ordered, 1)]
        for sibling, rank in ranks:
            if getattr(sibling, rank_field) != rank:
                before_state = _row_state(sibling)
                setattr(sibling, rank_field, rank)
                self.revision("reorder" if sibling is row else "normalize_order", sibling, before_state, _row_state(sibling))
        _flush(self.db)

    def reparent(self, row, parent):
        if isinstance(row, SpatialZone):
            if parent is not None and not isinstance(parent, SpatialZone):
                raise HTTPException(422, detail="A zone can only be parented to a zone or the scene.")
            new_parent = parent.id if parent is not None else None
            _check_zone_parent(self.db, self.scene.id, row.id, new_parent)
            patch = _preserving_patch(self.db, row, row.parent_zone_id, new_parent)
            SpatialService.update_zone(self.db, row.id, SpatialZoneUpdate(parent_zone_id=new_parent, **patch),
                                       self.user_id, commit=False)
        else:
            new_parent = parent.id if isinstance(parent, SpatialObject) else None
            zone_id = row.zone_id if new_parent is not None else (parent.id if parent is not None else None)
            self.move_object(row, new_parent, zone_id)

    def move_object(self, row, parent_id, zone_id):
        _check_object_parent(self.db, self.scene.id, row.id, parent_id)
        patch = _preserving_patch(self.db, row, row.parent_object_id, parent_id)
        self.update_object(row, parent_object_id=parent_id, zone_id=zone_id, **patch)

    def create_group(self, action):
        members = self.entities(action.members)
        data = action.group
        before = None
        if members:
            if any(not isinstance(row, SpatialObject) for row in members):
                raise HTTPException(422, detail="Only objects and groups can be grouped.")
            key = _parent_key(members[0])
            if any(_parent_key(row) != key for row in members):
                _conflict("Group members must be siblings. Reparent them first.")
            parent_id = members[0].parent_object_id
            zone_id = None if parent_id else members[0].zone_id
            if ("parent_object_id" in data.model_fields_set and data.parent_object_id != parent_id or
                "zone_id" in data.model_fields_set and data.zone_id != zone_id or
                data.transform.model_dump() != SpatialTransform().model_dump()):
                _conflict("Grouping siblings uses their existing hierarchy and an identity local transform.")
            data = data.model_copy(update={"parent_object_id": parent_id, "zone_id": zone_id})
            selected_ids = {row.id for row in members}
            siblings = _scene_siblings(self.db, self.scene.id, key)
            members = [row for row in siblings if isinstance(row, SpatialObject) and row.id in selected_ids]
            first = siblings.index(members[0])
            before = next((row for row in siblings[first + 1:] if not isinstance(row, SpatialObject)
                           or row.id not in selected_ids), None)
        group = SpatialService.create_object(
            self.db, self.scene.id, data, self.user_id, commit=False, entity_id=action.id,
        )
        for row in members:
            # An identity group inserted in the same transform frame preserves all authored values.
            self.update_object(row, parent_object_id=group["id"])
        if before is not None:
            self.reorder(self.group(group["id"]), before)
        return [group["id"]]

    def ungroup(self, group):
        require_branch_unlocked(self.db, group)
        link = self.db.scalar(select(SpatialObjectAsset.id).where(
            SpatialObjectAsset.scene_id == self.scene.id, SpatialObjectAsset.object_id == group.id,
        ).limit(1))
        edge = self.db.scalar(select(SpatialObjectRelationship.id).where(
            SpatialObjectRelationship.scene_id == self.scene.id,
            (SpatialObjectRelationship.source_object_id == group.id) | (SpatialObjectRelationship.target_object_id == group.id),
        ).limit(1))
        if link is not None or edge is not None:
            _conflict("Detach the group's own asset links and relationships before ungrouping.")
        children = _scene_siblings(self.db, self.scene.id, (SpatialObject, group.id))
        original_parent = _parent_key(group)
        for child in children:
            self.move_object(child, group.parent_object_id, child.zone_id)
            if _parent_key(child) == original_parent:
                self.reorder(child, group)
        SpatialService.delete_object(self.db, group.id, self.user_id, commit=False)
        return [child.id for child in children]

    def duplicate(self, group):
        require_branch_unlocked(self.db, group)
        pending, sources = deque([group]), []
        while pending:
            row = pending.popleft()
            sources.append(row)
            if len(sources) > 500:
                raise HTTPException(422, detail="A group duplicate supports up to 500 nodes.")
            pending.extend(_scene_siblings(self.db, self.scene.id, (SpatialObject, row.id)))
        ids = {row.id: uuid4() for row in sources}
        for row in sources:
            canonical = _object_response(self.db, row)
            fields = ("name", "category", "layer_id", "zone_id", "transform", "appearance", "properties", "tags", "status")
            data = {field: canonical[field] for field in fields}
            data["parent_object_id"] = ids.get(row.parent_object_id, row.parent_object_id)
            if row is group:
                data["name"] = row.name[:195] + " copy"
            if row.node_kind == "group":
                create = SpatialGroupCreate(**data)
            else:
                create = SpatialObjectCreate(**data, object_type=row.object_type, material_refs=row.material_refs,
                                             dimensions=canonical["dimensions"])
            origin = (row.library_definition_id, row.library_definition_version) if row.library_definition_id else None
            SpatialService.create_object(self.db, self.scene.id, create, self.user_id, commit=False, entity_id=ids[row.id],
                                         library_origin=origin)
        for link in self.db.scalars(select(SpatialObjectAsset).where(
            SpatialObjectAsset.scene_id == self.scene.id, SpatialObjectAsset.object_id.in_(ids),
        ).order_by(SpatialObjectAsset.id)):
            SpatialService.attach_asset(self.db, ids[link.object_id], SpatialAssetAttach(asset_id=link.asset_id, role=link.role),
                                        self.user_id, commit=False)
        for edge in self.db.scalars(select(SpatialObjectRelationship).where(
            SpatialObjectRelationship.scene_id == self.scene.id,
            SpatialObjectRelationship.source_object_id.in_(ids), SpatialObjectRelationship.target_object_id.in_(ids),
        ).order_by(SpatialObjectRelationship.id)):
            data = SpatialRelationshipCreate(source_object_id=ids[edge.source_object_id],
                                             target_object_id=ids[edge.target_object_id],
                                             relationship_type=edge.relationship_type, metadata=edge.metadata_json)
            SpatialService.create_relationship(self.db, self.scene.id, data, self.user_id, commit=False)
        return list(ids.values())

    def set_state(self, rows, *, lock_change=False, value=None):
        if not lock_change:
            require_unlocked(self.db, *rows)
        for row in rows:
            current = local_state(row)
            if lock_change:
                attr, desired = "is_locked", value
            else:
                visible = not current.visible if value is None else value
                if not isinstance(row, SpatialLayer) and row.status == "archived":
                    if visible:
                        _conflict("An archived node must be restored before it can be made visible.")
                    continue
                attr = "is_visible" if isinstance(row, SpatialLayer) else "status"
                desired = visible if isinstance(row, SpatialLayer) else ("active" if visible else "hidden")
            if getattr(row, attr) != desired:
                before = _row_state(row)
                setattr(row, attr, desired)
                self.revision("set_lock" if lock_change else "set_visibility", row, before, _row_state(row))
        _flush(self.db)

    def layer(self, action):
        if isinstance(action, schema.CreateLayer):
            row = SpatialLayer(id=action.id, scene_id=self.scene.id, name=action.name.strip(),
                               order_index=append_order(self.db, self.scene.id, layer=True),
                               is_visible=action.visible, metadata_json=action.metadata)
            self.db.add(row)
            _flush(self.db)
            self.revision("create", row, after=_row_state(row))
        else:
            row = scene_row(self.db, SpatialLayer, self.scene.id, action.id)
            require_unlocked(self.db, row)
            before = _row_state(row)
            if isinstance(action, schema.DeleteLayer):
                if row.is_default:
                    _conflict("Clear or change the default layer before deleting it.")
                exists = self.db.scalar(select(SpatialObject.id).where(
                    SpatialObject.scene_id == self.scene.id, SpatialObject.layer_id == row.id,
                ).limit(1))
                if exists is not None:
                    _conflict("Move or remove assigned nodes before deleting this layer.")
                self.revision("delete", row, before)
                self.db.delete(row)
            else:
                changes = action.model_dump(exclude_unset=True, exclude={"id", "kind"})
                if any(value is None for value in changes.values()):
                    raise HTTPException(422, detail="Layer properties cannot be null.")
                if "name" in changes:
                    row.name = changes["name"].strip()
                if "metadata" in changes:
                    row.metadata_json = changes["metadata"]
                if _row_state(row) != before:
                    self.revision("update", row, before, _row_state(row))
        _flush(self.db)
        return [row.id]

    def set_default_layer(self, layer_id):
        next_layer = scene_row(self.db, SpatialLayer, self.scene.id, layer_id)
        current_layer = default_layer(self.db, self.scene.id)
        if current_layer is not next_layer:
            require_unlocked(self.db, current_layer, next_layer)
            for layer, value in ((current_layer, False), (next_layer, True)):
                if layer is None:
                    continue
                before = _row_state(layer)
                layer.is_default = value
                self.revision("set_default_layer", layer, before, _row_state(layer))
                _flush(self.db)
        return [layer_id] if layer_id is not None else []

    def run(self, action):
        if isinstance(action, schema.CreateGroup):
            return self.create_group(action)
        if isinstance(action, schema.UpdateGroup):
            group = self.group(action.id)
            self.update_object(group, **action.model_dump(exclude_unset=True, exclude={"id", "kind"}))
            return [group.id]
        if isinstance(action, schema.GroupAction):
            group = self.group(action.id)
            if action.kind == "ungroup":
                return self.ungroup(group)
            if action.kind == "duplicate_group":
                return self.duplicate(group)
            SpatialService.delete_object(self.db, group.id, self.user_id, commit=False)
            return [group.id]
        if isinstance(action, schema.Reparent):
            row = self.entity(action.node)
            self.reparent(row, self.entity(action.parent) if action.parent is not None else None)
            return [row.id]
        if isinstance(action, schema.Reorder):
            row = self.entity(action.node)
            self.reorder(row, self.entity(action.before) if action.before is not None else None, scope=action.scope)
            return [row.id]
        if isinstance(action, (schema.SetLocked, schema.SetVisibility)):
            rows = self.entities(action.nodes)
            if isinstance(action, schema.SetLocked):
                self.set_state(rows, lock_change=True, value=action.locked)
            else:
                self.set_state(rows, value=action.visible)
            return [row.id for row in rows]
        if isinstance(action, schema.SetDefaultLayer):
            return self.set_default_layer(action.layer_id)
        if isinstance(action, schema.AssignLayer):
            rows = self.entities(action.nodes)
            if any(not isinstance(row, SpatialObject) for row in rows):
                raise HTTPException(422, detail="Only objects and groups can be assigned to a layer.")
            scene_row(self.db, SpatialLayer, self.scene.id, action.layer_id)
            for row in rows:
                self.update_object(row, layer_id=action.layer_id)
            return [row.id for row in rows]
        return self.layer(action)


def apply(db, scene_id, user_id, change):
    scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
    if scene.current_revision != change.base_revision:
        _conflict("The scene changed elsewhere. Reload the scene graph and use its current revision.")
    writer = GraphWrite(db, scene, user_id)
    try:
        results = [{"kind": action.kind, "ids": writer.run(action)} for action in change.operations]
        response = {"scene_revision": scene.current_revision, "results": results}
        _commit(db)
        return response
    except Exception:
        db.rollback()
        raise
