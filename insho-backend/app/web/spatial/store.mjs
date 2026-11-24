const fields = {
  object: ["zone_id", "parent_object_id", "object_type", "category", "name", "transform", "dimensions", "appearance",
    "material_refs", "properties", "tags", "status"],
  zone: ["parent_zone_id", "name", "category", "description", "transform", "dimensions", "footprint",
    "appearance", "metadata", "status"],
  relationship: ["source_object_id", "target_object_id", "relationship_type", "metadata"],
};

export const canonical = (kind, row) => Object.fromEntries(fields[kind].map(key => [key, structuredClone(row[key])]));
export const operation = (kind, id, data = {}) => ({kind, id, data});
export const attach = (asset, id = asset.id, objectId = asset.object_id) => ({
  ...operation("attach_asset", id, {asset_id: asset.asset_id, role: asset.role}), object_id: objectId,
});

export function inverse(snapshot, operations) {
  const undo = [], relations = new Map(), assets = [];
  for (const op of [...operations].reverse()) {
    const [verb, kind] = op.kind.split("_");
    const rows = snapshot[kind + "s"] || [];
    const row = kind === "scene" ? snapshot.scene : rows.find(r => r.id === op.id);
    if (op.kind === "attach_asset") undo.push(operation("detach_asset", op.id));
    else if (op.kind === "detach_asset") undo.push(attach(snapshot.assets.find(a => a.id === op.id)));
    else if (verb === "create") undo.push(operation("delete_" + kind, op.id));
    else if (verb === "update") undo.push(operation(op.kind, op.id,
      Object.fromEntries(Object.keys(op.data).map(key => [key, structuredClone(row[key])]))));
    else if (verb === "delete") {
      undo.push(operation("create_" + kind, op.id, canonical(kind, row)));
      if (kind === "object") {
        for (const asset of snapshot.assets.filter(a => a.object_id === op.id)) assets.push(attach(asset));
        for (const edge of snapshot.relationships.filter(e => e.source_object_id === op.id || e.target_object_id === op.id)) {
          relations.set(edge.id, operation("create_relationship", edge.id, canonical("relationship", edge)));
        }
      }
    }
  }
  return [...undo, ...assets, ...relations.values()];
}

export class SceneStore {
  constructor(request, snapshot, changed) {
    this.request = request;
    this.snapshot = snapshot;
    this.changed = changed;
    this.undo = [];
    this.redo = [];
    this.pending = null;
    this.status = "saved";
    this.error = "";
  }
  begin(operations, history = null) {
    if (!operations.length || this.pending || !this.snapshot.can_edit) return;
    const entry = history?.entry || {redo: operations, undo: inverse(this.snapshot, operations)};
    this.pending = {id: crypto.randomUUID(), body: {base_revision: this.snapshot.scene.current_revision, operations},
      entry, direction: history?.direction || "new"};
    this.status = "unsaved";
    this.error = "";
    this.changed();
    this.timer = setTimeout(() => this.save(), 100);
  }
  step(direction) {
    const entry = this[direction].at(-1);
    if (entry) this.begin(entry[direction], {entry, direction});
  }
  async save() {
    if (!this.pending || this.status === "saving") return;
    clearTimeout(this.timer);
    const {id, body, entry, direction} = this.pending;
    this.status = "saving";
    this.error = "";
    this.changed();
    try {
      const result = await this.request("/spatial/scenes/" + this.snapshot.scene.id + "/editor/changes/" + id, "PUT", body);
      this.snapshot = result.snapshot;
      if (result.committed_revision !== result.snapshot.scene.current_revision) {
        this.undo = [];
        this.redo = [];
        this.error = "Your action saved. The scene then changed elsewhere, so local undo history was cleared.";
      } else {
        if (direction === "new") {
          this.undo.push(entry);
          this.undo = this.undo.slice(-60);
          this.redo = [];
        } else {
          this[direction].pop();
          this[direction === "undo" ? "redo" : "undo"].push(entry);
        }
      }
      this.pending = null;
      this.status = "saved";
    } catch (error) {
      this.status = "failed";
      this.error = error.message;
    }
    this.changed();
  }
  async reload() {
    const snapshot = await this.request("/spatial/scenes/" + this.snapshot.scene.id + "/editor");
    clearTimeout(this.timer);
    this.snapshot = snapshot;
    this.pending = null;
    this.undo = [];
    this.redo = [];
    this.status = "saved";
    this.error = "";
    this.changed();
  }
}
