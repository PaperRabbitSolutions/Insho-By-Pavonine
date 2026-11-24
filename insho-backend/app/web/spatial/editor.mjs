import {el, button, choice, number, dialog, replace} from "./dom.mjs";
import {add, distance, handleRotation, localDelta, localXY, nearestGrid, pivot, point, previewSnap, projected, round, sub, world, xy} from "./geometry.mjs";
import {attach, canonical, operation, SceneStore} from "./store.mjs";
import {Viewport} from "./viewport.mjs";
import {Inspector} from "./inspector.mjs";

const objects = {
  stage:[6,4,.6], platform:[3,2,.3], mandap:[4,4,3], seating_block:[6,4,1], chair:[.5,.5,.9], table:[1.8,.8,.75],
  sofa:[2,.9,.85], furniture:[1,1,.8], floral_installation:[1,1,1.8], decorative_element:[.6,.6,1], arch:[2,.55,2.5],
  backdrop:[4,.4,2.8], led_screen:[3,.4,2], lighting_element:[.6,.6,2.5], truss:[4,.45,3], structure:[3,3,3],
  flooring:[4,4,.05], ceiling_structure:[4,4,.3], entrance:[3,1,2.6], signage:[.8,.35,1.6], counter:[2,.7,1],
  bar:[2.5,.8,1.1], dance_floor:[5,5,.08], path:[4,1.5,.03], custom:[1,1,1],
};
const zoneTypes = ["stage_area","guest_seating","vip_seating","dining","entry","reception","dance_floor","lounge",
  "backstage","service_area","bar","photography_area","restricted_area","custom"];
export const label = value => value.replaceAll("_", " ").replace(/^./, s => s.toUpperCase());
const saveLabels = {saved:"Saved", unsaved:"Unsaved changes", saving:"Saving…", failed:"Save failed"};
const rectangle = (a, b) => [a, point(b.x,a.y), b, point(a.x,b.y)];
const parentId = row => row.parent_object_id || row.parent_zone_id;
const keyOf = row => Object.hasOwn(row, "parent_zone_id") ? "zone" : "object";
const blankSettings = {grid:true, spacing:1, snapGrid:true, snapObjects:true, snapAngle:true, hidden:[], clearance_m:0};
const actionKeys = {v:"select", h:"pan", m:"move", r:"rotate", o:"object", z:"zone", l:"measure"};
const editTools = new Set(["move", "rotate", "object", "zone"]);

export class Editor {
  constructor(root, request, snapshot, project, reauthenticate) {
    this.root = root;
    this.request = request;
    this.project = project;
    this.reauthenticate = reauthenticate;
    this.tool = "select";
    this.category = "stage";
    this.zoneType = "guest_seating";
    this.selection = new Set();
    this.issueIds = new Set();
    this.store = new SceneStore(request, snapshot, () => this.sync());
    this.ref = snapshot;
    this.validation = null;
    this.path = "/spatial/scenes/" + snapshot.scene.id;
    this.shortcuts = e => this.key(e);
    this.guard = e => {
      if (this.busy || this.drag || this.inspector?.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    this.mount();
    this.sync(true);
    window.addEventListener("beforeunload", this.guard);
    document.addEventListener("keydown", this.shortcuts);
    this.validate();
  }
  get data() {return this.store.snapshot;}
  get settings() {
    const raw=this.data.scene.metadata?.editor || {};
    const booleanKeys=["grid","snapGrid","snapObjects","snapAngle"];
    return {...blankSettings, ...Object.fromEntries(booleanKeys.filter(key=>typeof raw[key]==="boolean").map(key=>[key,raw[key]])),
      spacing:Number.isFinite(raw.spacing)&&raw.spacing>=.000001&&raw.spacing<=100000?raw.spacing:1,
      hidden:Array.isArray(raw.hidden)?raw.hidden.filter(x=>typeof x==="string"):[],
      clearance_m:Number.isFinite(raw.clearance_m)&&raw.clearance_m>=0?raw.clearance_m:0};
  }
  get unit() {return this.data.scene.display_unit;}
  get factor() {return this.data.meters_per_unit[this.unit];}
  get busy() {return Boolean(this.store.pending || this.calculating || this.retryCalculation);}
  get editable() {return this.data.can_edit && !this.busy;}
  isZone(row) {return keyOf(row) === "zone";}
  visibleRows() {return this.visible;}
  selected() {return [...this.selection].map(id => this.rows.get(id)).filter(Boolean);}
  format(meters) {
    return new Intl.NumberFormat(undefined, {maximumSignificantDigits: 5}).format(meters / this.factor) + " " + this.unit;
  }
  mount() {
    this.root.className = "editor";
    this.toolBar = el("div", {class:"row", role:"toolbar", "aria-label":"Editing tools"});
    this.actionBar = el("div", {class:"row", role:"toolbar", "aria-label":"Scene actions"});
    this.context = el("div", {class:"contextbar"});
    this.notice = el("div", {class:"notice", role:"alert", hidden:true});
    this.layers = el("aside", {class:"sidebar", "aria-label":"Layers"});
    this.panel = el("aside", {class:"sidebar inspector", "aria-label":"Inspector"});
    this.zoomText = button("100%", () => this.viewport.zoomBy(this.viewport.initialScale / this.viewport.camera.scale), {title:"Reset zoom"});
    this.scaleText = el("output", {class:"scale", "aria-label":"Map scale"});
    const canvas = document.createElementNS("http://www.w3.org/2000/svg","svg");
    for (const [attr, value] of Object.entries({class:"viewport", tabindex:"0", role:"region", "aria-label":"Spatial canvas"})) canvas.setAttribute(attr,value);
    this.empty = el("p", {class:"empty-plan"}, "Empty scene. Choose Object or Zone, then click or drag on the plan.");
    this.canvas = el("section", {class:"canvas-wrap"}, canvas, this.empty, el("div", {class:"viewport-controls"},
      button("−", () => this.viewport.zoomBy(1/1.25), {"aria-label":"Zoom out"}), this.zoomText,
      button("+", () => this.viewport.zoomBy(1.25), {"aria-label":"Zoom in"}), button("Fit scene", () => this.viewport.fit("scene")),
      button("Fit content", () => this.viewport.fit("content")), button("Fit selection", () => this.viewport.fit("selection")),
      button("Center", () => this.viewport.center()), this.scaleText));
    this.root.replaceChildren(el("div", {class:"toolbar"}, this.toolBar, this.actionBar), this.context, this.notice,
      el("div", {class:"workbench"}, this.layers, this.canvas, this.panel));
    this.viewport = new Viewport(canvas, this);
    this.inspector = new Inspector(this.panel, this);
  }
  sync(initial = false) {
    if (this.ref !== this.data || initial) {
      this.ref = this.data;
      this.rows = new Map([...this.data.zones, ...this.data.objects].map(row => [row.id,row]));
      const hidden = new Set(this.settings.hidden);
      const visibleIds = new Map();
      const shown = row => {
        if (!row) return true;
        if (!visibleIds.has(row.id)) visibleIds.set(row.id, row.status === "active" &&
          !hidden.has(keyOf(row) + ":" + row.category) && shown(this.rows.get(parentId(row))));
        return visibleIds.get(row.id);
      };
      this.visible = [...this.rows.values()].filter(shown);
      this.selection = new Set([...(this.nextSelection || this.selection)].filter(id => this.rows.has(id)));
      this.nextSelection = null;
      this.preview = null;
      this.inspector.render();
      this.renderLayers();
      if (!initial) this.validate();
    }
    this.inspector.lock(this.busy);
    this.renderBars();
    this.renderNotice();
    this.empty.hidden = this.rows.size > 0 || !this.data.can_edit;
    this.viewport.node.dataset.tool = this.tool;
    this.viewport.schedule();
  }
  renderNotice() {
    const message = this.store.error || this.error || "";
    this.notice.hidden = !message;
    this.notice.replaceChildren(message);
    if (this.retryCalculation) {
      this.notice.append(" ", button("Retry edit", () => {
        const action=this.retryCalculation;
        this.retryCalculation=null;
        this.run(action, true, true);
      }), " ", button("Sign in again", this.reauthenticate), " ", button("Discard pending edit", () => {
        this.retryCalculation=null;
        this.preview=null;
        this.inspector.preview(null);
        this.message("");
        this.sync();
      }));
    } else if (this.store.status === "failed") {
      this.notice.append(" ", button("Retry save", () => this.store.save()));
      this.notice.append(" ", button("Sign in again", this.reauthenticate));
      this.notice.append(" ", button("Discard pending and reload", async () => {
        if (confirm("Discard the pending edit and load the saved scene?")) this.run(() => this.store.reload(), false);
      }));
    }
  }
  renderBars() {
    this.toolBar.replaceChildren(...Object.entries(actionKeys).filter(([,tool]) => this.data.can_edit || !editTools.has(tool))
      .map(([key, tool]) => button(label(tool), () => this.setTool(tool),
        {"aria-pressed":String(this.tool === tool), title:label(tool) + " (" + key.toUpperCase() + ")"})));
    const disabled = !this.editable;
    this.actionBar.replaceChildren(
      button("Undo", () => this.store.step("undo"), {disabled:disabled || this.inspector.dirty || !this.store.undo.length, title:"Ctrl/Cmd+Z"}),
      button("Redo", () => this.store.step("redo"), {disabled:disabled || this.inspector.dirty || !this.store.redo.length, title:"Ctrl/Cmd+Shift+Z"}),
      button("Duplicate", () => this.duplicate(), {disabled:disabled || !this.selection.size}),
      button("Delete", () => this.remove(), {disabled:disabled || !this.selection.size}),
      this.panelToggle("Layers",this.layers),
      this.panelToggle("Inspector",this.panel),
      button("Plan reference", () => this.plan()), button("Shortcuts", () => this.help()),
      el("output", {class:"save", role:"status", "data-state":this.store.status, "data-revision":this.data.scene.current_revision}, this.calculating ? "Calculating…" : this.retryCalculation ? "Edit failed" :
        this.inspector?.dirty && !this.store.pending ? "Unsaved changes" : saveLabels[this.store.status]),
      button("Save", () => this.save(), {class:"primary", disabled:!this.store.pending && !this.inspector?.dirty || this.store.status === "saving"}));
    const checkbox = (caption, key) => el("label", {class:"check"}, el("input", {type:"checkbox", checked:this.settings[key],
      disabled, on:{change:e => this.setting({[key]:e.target.checked})}}), caption);
    const spacing = number("grid-spacing", this.settings.spacing / this.factor, {required:true, min:.000001 / this.factor});
    const unit = choice("unit", Object.keys(this.data.meters_per_unit).map(x => [x,x]), this.unit,
      {disabled, on:{change:e => this.updateScene({display_unit:e.target.value})}});
    const alignment = choice("alignment", [["","Align selection…"],["left","Left edges"],["right","Right edges"],["top","Top edges"],
      ["bottom","Bottom edges"],["center_x","Horizontal centers"],["center_y","Vertical centers"],["center","Same center"],
      ["x","Equal spacing X"],["y","Equal spacing Y"]], "", {disabled:disabled || this.selection.size < 2,
      "aria-label":"Align selection", on:{change:e => this.align(e.target.value)}});
    replace(this.context,
      this.tool === "object" ? choice("placement", Object.keys(objects).map(x => [x, label(x)]), this.category,
        {"aria-label":"Object category to place", on:{change:e => {this.category=e.target.value;}}}) : null,
      this.tool === "zone" ? choice("zone-type", zoneTypes.map(x => [x,label(x)]), this.zoneType,
        {"aria-label":"Zone category to place", on:{change:e => {this.zoneType=e.target.value;}}}) : null,
      checkbox("Grid", "grid"), checkbox("Snap grid", "snapGrid"), checkbox("Snap objects", "snapObjects"), checkbox("15° snap", "snapAngle"),
      el("form", {class:"row", on:{submit:e => {e.preventDefault(); this.spacing(+spacing.value * this.factor);}}},
        choice("grid-preset", [["","Grid spacing…"],...([.1,.25,.5,1,2,5].map(n=>[n,this.format(n)]))], "",
          {disabled, "aria-label":"Grid spacing preset", on:{change:e => {if (e.target.value) this.spacing(+e.target.value);}}}),
        spacing, button("Set", () => {if (spacing.reportValidity()) this.spacing(+spacing.value*this.factor);}, {disabled}),
        el("span", {class:"hint"}, this.unit)),
      el("label", {class:"check"}, "Display unit", unit), alignment,
      this.tool === "object" || this.tool === "zone" ? button("Add at scene center", () => {
        const b = this.viewport.bounds(), p = point(b.max_x_m / 2,b.max_y_m / 2);
        this.place(this.tool,p,p);
      }, {disabled}) : null,
      this.tool === "measure" ? el("span", {class:"hint"}, "Drag between two points to measure.") : null);
  }
  panelToggle(name, panel) {
    return button(name, event => {
      panel.hidden = !panel.hidden;
      event.currentTarget.setAttribute("aria-expanded",String(!panel.hidden));
    }, {"aria-expanded":String(!panel.hidden)});
  }
  help() {
    dialog("Keyboard shortcuts", el("ul", {}, [
      "V select · H pan · M move · R rotate · O object · Z zone · L measure",
      "Shift+click adds or removes from the selection. Alt+click cycles overlapping shapes.",
      "Arrow keys nudge the selection. Hold Shift for a larger step.",
      "[ and ] rotate. + and − zoom. Space+drag pans. Escape cancels or clears selection.",
      "Delete removes selected layers. Ctrl/Cmd+D duplicates. Ctrl/Cmd+S saves.",
      "Ctrl/Cmd+Z undoes. Ctrl/Cmd+Shift+Z redoes.",
    ].map(t=>el("li",{},t))));
  }
  spacing(n) {
    if (n >= .000001 && n <= 100000 && Number.isFinite(n)) this.setting({spacing:n});
    else this.message("Grid spacing must be between 0.000001 m and 100,000 m.");
  }
  setting(patch) {this.updateScene({metadata:{...this.data.scene.metadata, editor:{...this.settings,...patch}}});}
  updateScene(data) {this.commit([operation("update_scene",this.data.scene.id,data)]);}
  setTool(tool) {
    this.cancel();
    this.tool = tool;
    this.sync();
  }
  message(text) {this.error = text; this.renderNotice();}
  async run(action, lock = true, retry = false) {
    if (lock) {
      if (this.busy) return;
      this.calculating = true;
      this.sync();
    }
    this.message("");
    try {return await action();}
    catch (error) {
      if (retry && (!error.status || error.status === 401 || error.status >= 500)) this.retryCalculation=action;
      this.message(error.message);
    }
    finally {if (lock) {this.calculating = false; this.sync();}}
  }
  commit(operations, selected = null) {
    if (this.store.pending || !this.data.can_edit) return;
    if (this.inspector.dirty) {
      this.message("Apply or cancel the inspector edits before changing the scene.");
      return;
    }
    if (selected) this.nextSelection = selected;
    this.error = "";
    this.store.begin(operations);
  }
  save() {
    if (this.store.pending) this.store.save();
    else if (this.inspector.dirty) this.inspector.form.requestSubmit();
  }
  select(id, additive = false) {
    if (this.busy) return false;
    if (this.inspector.dirty) {this.message("Apply or cancel the inspector edits before changing the selection."); return false;}
    if (!additive) this.selection.clear();
    if (id && additive && this.selection.has(id)) this.selection.delete(id);
    else if (id) this.selection.add(id);
    this.inspector.render();
    this.renderLayers();
    this.renderBars();
    this.viewport.schedule();
    return true;
  }
  renderLayers() {
    const previouslyClosed = new Set([...this.layers.querySelectorAll("details:not([open])")].map(node=>node.dataset.group));
    const groups = new Map();
    for (const row of this.rows.values()) {
      const key = keyOf(row) + ":" + row.category;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }
    const rowEntry = row => el("li",{class:"layer"},
      button(row.name,e=>this.select(row.id,e.shiftKey),{"aria-pressed":String(this.selection.has(row.id)), title:row.name}),
      button(row.status === "active" ? "Hide" : "Show", () => this.commit([operation("update_"+keyOf(row),row.id,
        {status:row.status === "active" ? "hidden" : "active"})]), {disabled:!this.data.can_edit,
        "aria-label":(row.status === "active" ? "Hide " : "Show ") + row.name}));
    const sections = [...groups].map(([key,rows]) => {
      const hidden = this.settings.hidden.includes(key), [kind,category] = key.split(":");
      return el("details",{open:!previouslyClosed.has(key),"data-group":key},el("summary",{},(kind === "zone" ? "Zone · " : "") + label(category)),
        button(hidden ? "Show category" : "Hide category", () => this.setting({hidden:hidden ? this.settings.hidden.filter(x=>x!==key) :
          [...this.settings.hidden,key]}), {disabled:!this.data.can_edit}), el("ul",{},rows.map(rowEntry)));
    });
    this.layers.replaceChildren(el("h2",{},"Layers"), ...(sections.length ? sections : [el("p",{class:"hint"},"Add an object or zone to organize it here.")]));
  }
  parentMatrix(row) {return this.data.geometry[parentId(row)]?.matrix;}
  canHandle(row) {
    const g=this.data.geometry[row.id], parent=this.parentMatrix(row);
    return this.editable && this.visibleRows().includes(row) && row.dimensions && g?.bounds &&
      (this.isZone(row) ? !row.footprint : ["box","rectangle","rect"].includes(row.properties.geometry?.type || row.properties.geometry_type || "box")) &&
      !+row.transform.rotation_deg.x && !+row.transform.rotation_deg.y &&
      (!parent || Math.abs(parent[2][0]) < 1e-10 && Math.abs(parent[2][1]) < 1e-10 &&
        Math.abs(parent[0][2]) < 1e-10 && Math.abs(parent[1][2]) < 1e-10);
  }
  roots(rows) {
    const ids=new Set(rows.map(r=>r.id));
    return rows.filter(row => {
      for (let id=parentId(row); id; id=parentId(this.rows.get(id))) if (ids.has(id)) return false;
      return true;
    });
  }
  descendants(ids) {
    const found = new Set(ids);
    for (const row of this.rows.values()) {
      for (let id=parentId(row); id; id=parentId(this.rows.get(id))) {
        if (found.has(id)) {found.add(row.id); break;}
      }
    }
    return found;
  }
  translation(row, delta) {
    const p=row.transform.position, d=localDelta(this.parentMatrix(row),delta);
    return {...row.transform,position:{x:round(+p.x+d.x),y:round(+p.y+d.y),z:round(+p.z+d.z)}};
  }
  moveOperations(deltas) {
    return [...deltas].map(([row,delta]) => operation("update_"+keyOf(row),row.id,{transform:this.translation(row,delta)}));
  }
  snapConfig(excluded = new Set()) {
    const visible=new Set(this.visibleRows().map(row=>row.id));
    return {spacing:this.settings.snapGrid ? this.settings.spacing : null,
      excluded:this.settings.snapObjects ? [...excluded] : [],
      anchors:this.settings.snapObjects ? this.data.objects.filter(o=>!excluded.has(o.id) && visible.has(o.id))
        .flatMap(o=>(this.data.geometry[o.id]?.anchors || []).map(a=>({...a,id:o.id}))) : [],
      limit:8/this.viewport.camera.scale};
  }
  async serverSnap(p, config) {
    if (!config.spacing && !this.settings.snapObjects) return p;
    if (config.excluded.length > 500) throw new Error("Snapping supports up to 500 excluded objects. Turn off snapping to continue.");
    const result=await this.request(this.path+"/snap","POST",{position:{x_m:p.x,y_m:p.y},grid_spacing_m:config.spacing,
      anchor_tolerance_m:config.limit,exclude_object_ids:config.excluded,include_hidden:false,use_anchors:this.settings.snapObjects});
    return xy(result.point);
  }
  cancel() {
    if (this.drag && this.viewport.node.hasPointerCapture(this.drag.id)) this.viewport.node.releasePointerCapture(this.drag.id);
    this.drag=null;
    if (!this.busy) this.preview=null;
    this.measure=null;
    delete this.viewport.node.dataset.drag;
    this.inspector?.preview(null);
    this.viewport.schedule();
  }
  pointer(event) {
    const node=this.viewport.node, screenPoint=this.viewport.pointer(event), p=world(this.viewport.camera,screenPoint);
    if (event.type === "pointerdown") {
      if (this.drag || event.button !== 0 && event.button !== 1) return;
      node.focus({preventScroll:true});
      const pan=this.tool === "pan" || event.button === 1 || this.space;
      const mode=pan ? "pan" : this.tool;
      if (this.busy && !["pan","select","measure"].includes(mode)) return;
      const start={id:event.pointerId,screen:screenPoint,point:p,mode,camera:{...this.viewport.camera}};
      const handle=!pan && event.target.dataset.handle, row=this.selected()[0];
      if (handle && row && this.canHandle(row)) {
        Object.assign(start,{mode:handle,row,sign:point(+event.target.dataset.x,+event.target.dataset.y),
          geo:this.data.geometry[row.id]});
      } else if (["object","zone"].includes(mode) && this.editable && !this.inspector.dirty) {
        start.snap=this.snapConfig();
      } else if (mode === "measure") {
        this.measure={start:p,end:p};
      } else if (mode !== "pan") {
        const hits=this.viewport.getHits(p);
        let target=hits[0];
        if (event.altKey && hits.length > 1) {
          const index=hits.findIndex(candidate=>this.selection.has(candidate.id));
          target=hits[(index+1)%hits.length];
        }
        if (target && this.selection.has(target.id) && !event.shiftKey && !event.altKey && mode !== "rotate") {
          start.singleOnClick=target.id;
        } else if (!this.select(target?.id,event.shiftKey)) return;
        if (!target || event.shiftKey || event.altKey || !this.editable) return;
        if (mode === "rotate" && this.canHandle(target)) {
          Object.assign(start,{mode:"rotate",row:target,geo:this.data.geometry[target.id]});
        } else if (mode === "select" || mode === "move") {
          start.mode="move";
          start.rows=this.roots(this.selected().filter(r=>this.visibleRows().includes(r)));
          start.anchor=target;
          start.affected=this.descendants(start.rows.map(r=>r.id));
          start.snap=this.snapConfig(new Set(start.rows.map(row=>row.id)));
          start.snap.anchors=start.snap.anchors.filter(anchor=>!start.affected.has(anchor.id));
        } else return;
      }
      this.drag=start;
      node.dataset.drag=start.mode;
      node.setPointerCapture(event.pointerId);
      event.preventDefault();
      this.viewport.schedule();
      return;
    }
    const d=this.drag;
    if (!d || d.id !== event.pointerId) return;
    if (event.type === "pointercancel") {this.cancel();return;}
    if (event.type === "pointermove" || event.type === "pointerup") {
      const moved=distance(d.screen,screenPoint)>3;
      if (d.mode === "pan") {
        const delta=sub(screenPoint,d.screen);
        this.viewport.camera={...d.camera,x:d.camera.x+delta.x,y:d.camera.y+delta.y};
      } else if (d.mode === "measure") {
        this.measure={start:d.point,end:p};
      } else if (d.mode === "move" && moved) {
        const center=pivot(this.data.geometry[d.anchor.id]), raw=add(center,sub(p,d.point));
        const snap=this.moveSnap(d,raw);
        const delta=sub(snap.result.point,snap.source);
        this.preview={translations:new Map([...d.affected].map(id=>[id,delta])),
          guide:snap.result.kind ? snap.result.point : null};
        this.inspector.preview(this.translation(d.anchor,delta).position);
      } else if (d.mode === "resize" && moved) {
        const {width_m:w,depth_m:v}=this.resizeDimensions(d,p,event.shiftKey);
        this.preview={shapes:new Map([[d.row.id,this.boxPreview(d.geo.matrix,w,v)]])};
        this.inspector.preview(null,{width_m:w,depth_m:v});
      } else if (d.mode === "rotate" && moved) {
        const degrees=this.rotation(d,p,event.shiftKey);
        this.preview={shapes:this.rotatePreview(d,degrees)};
        this.inspector.preview(null,null,degrees);
      } else if (["object","zone"].includes(d.mode) && moved) {
        const a=previewSnap(d.point,d.snap.spacing,d.snap.anchors,d.snap.limit).point;
        const b=previewSnap(p,d.snap.spacing,d.snap.anchors,d.snap.limit).point;
        this.preview={draft:rectangle(a,b),guide:b};
      }
      this.viewport.schedule();
      if (event.type === "pointerup") {
        this.drag=null;
        delete node.dataset.drag;
        if (node.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
        this.finish(d,p,moved,event.shiftKey);
      }
    }
  }
  async finish(d,p,moved,shift) {
    if (d.mode === "pan" || d.mode === "measure") return;
    if (["object","zone"].includes(d.mode)) await this.place(d.mode,d.point,moved?p:d.point,d.snap);
    else if (d.mode === "move" && moved) {
      await this.run(async () => {
        const center=pivot(this.data.geometry[d.anchor.id]), raw=add(center,sub(p,d.point));
        const snap=this.moveSnap(d,raw);
        const final=await this.serverSnap(snap.raw,d.snap);
        const delta=sub(final,snap.source);
        this.preview={translations:new Map([...d.affected].map(id=>[id,delta])),guide:snap.result.kind ? final : null};
        this.commit(this.moveOperations(d.rows.map(row=>[row,delta])));
      }, true, true);
    } else if (d.mode === "resize" && moved) {
      const dimensions=this.resizeDimensions(d,p,shift);
      this.commit([operation("update_"+keyOf(d.row),d.row.id,{dimensions})]);
    } else if (d.mode === "rotate" && moved) {
      this.commit([this.rotateOperation(d.row,this.rotation(d,p,shift))]);
    } else if (d.singleOnClick) this.select(d.singleOnClick);
    if (!this.busy) {this.preview=null;this.inspector.preview(null);}
    this.viewport.schedule();
  }
  moveSnap(d,raw) {
    const geo=this.data.geometry[d.anchor.id], center=pivot(geo);
    const choices=[center,...(geo.anchors || []).map(xy)].map(source=>{
      const candidate=add(raw,sub(source,center));
      return {source,raw:candidate,result:previewSnap(candidate,null,d.snap.anchors,d.snap.limit)};
    }).filter(candidate=>candidate.result.kind);
    choices.sort((a,b)=>distance(a.result.point,a.raw)-distance(b.result.point,b.raw));
    return choices[0] || {source:center,raw,result:previewSnap(raw,d.snap.spacing,[],d.snap.limit)};
  }
  resizeDimensions(d,p,disableSnap) {
    const a=localXY(d.geo.matrix,p), size=d.row.dimensions;
    const w=Math.max(.001,2*a.x*d.sign.x), h=Math.max(.001,2*a.y*d.sign.y);
    const edgeScale=i=>Math.hypot(d.geo.matrix[0][i],d.geo.matrix[1][i]);
    const snap=(n,i)=>{
      if (!this.settings.snapGrid || disableSnap) return round(n);
      const scale=edgeScale(i);
      return round(Math.max(.001, nearestGrid(n*scale,this.settings.spacing)/scale));
    };
    return {...size,width_m:snap(w,0),depth_m:snap(h,1)};
  }
  boxPreview(matrix,w,d) {
    return [[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>projected(matrix,x*w/2,y*d/2));
  }
  rotation(d,p,shift) {
    return round(+d.row.transform.rotation_deg.z + handleRotation(this.parentMatrix(d.row),pivot(d.geo),
      d.point,p,this.settings.snapAngle && !shift ? 15 : null));
  }
  rotateOperation(row,degrees) {
    return operation("update_"+keyOf(row),row.id,{transform:{...row.transform,
      rotation_deg:{...row.transform.rotation_deg,z:round(degrees)}}});
  }
  rotatePreview(d,degrees) {
    const origin=pivot(d.geo), parent=this.parentMatrix(d.row);
    const angle=(degrees - +d.row.transform.rotation_deg.z)*Math.PI/180, cos=Math.cos(angle),sin=Math.sin(angle);
    const shapes=new Map();
    for (const id of this.descendants([d.row.id])) {
      const geo=this.data.geometry[id];
      if (!geo?.bounds) continue;
      const vertices=geo.geometry_type==="circle" ? Array.from({length:32},(_,i)=>{
        const t=i*Math.PI/16,c=xy(geo.center);
        return point(c.x+geo.radius_m*Math.cos(t),c.y+geo.radius_m*Math.sin(t));
      }) : geo.vertices.map(xy);
      shapes.set(id,vertices.map(p=>{
        const local=localDelta(parent,sub(p,origin));
        const rotated=point(cos*local.x-sin*local.y,sin*local.x+cos*local.y);
        return add(origin,parent ? sub(projected(parent,rotated.x,rotated.y),pivot({matrix:parent})) : rotated);
      }));
    }
    return shapes;
  }
  async place(kind,start,end,config=this.snapConfig()) {
    if (!this.editable || this.inspector.dirty) return;
    const zone=kind==="zone", category=zone?this.zoneType:this.category;
    await this.run(async()=>{
      const a=await this.serverSnap(start,config), b=start===end? a:await this.serverSnap(end,config);
      let [w,d,h]=zone?[4,3,.1]:objects[category];
      let center=a;
      if (Math.abs(a.x-b.x)>.001 && Math.abs(a.y-b.y)>.001) {
        w=Math.abs(a.x-b.x);d=Math.abs(a.y-b.y);
        center=point((a.x+b.x)/2,(a.y+b.y)/2);
      }
      const transform={position:{x:round(center.x),y:round(center.y),z:zone?0:round(h/2)}};
      const id=crypto.randomUUID(), data={name:label(category),category,transform,
        dimensions:{width_m:round(w),depth_m:round(d),height_m:h}};
      if (!zone) data.object_type=category;
      this.preview={draft:rectangle(point(center.x-w/2,center.y-d/2),point(center.x+w/2,center.y+d/2))};
      this.commit([operation("create_"+kind,id,data)],[id]);
    }, true, true);
    if (!this.busy) this.preview=null;
    this.viewport.schedule();
  }
  deletionOrder(rows) {
    const depth = row => {
      let count=0;
      for (let id=parentId(row);id;id=parentId(this.rows.get(id))) count++;
      return count;
    };
    return [...rows].sort((a,b)=>Number(this.isZone(a))-Number(this.isZone(b)) || depth(b)-depth(a));
  }
  remove() {
    const selected=this.selected();
    if (!selected.length || !this.editable) return;
    this.commit(this.deletionOrder(selected).map(row=>operation("delete_"+keyOf(row),row.id)),[]);
  }
  duplicate() {
    if (!this.editable || !this.selection.size) return;
    const ids=new Map(this.selected().map(row=>[row.id,crypto.randomUUID()]));
    const operations=[];
    for (const row of this.deletionOrder(this.selected()).reverse()) {
      const kind=keyOf(row), data=canonical(kind,row), parent=parentId(row), id=ids.get(row.id);
      if (ids.has(parent)) data[this.isZone(row)?"parent_zone_id":"parent_object_id"]=ids.get(parent);
      else data.transform=this.translation(row,point(this.settings.spacing,this.settings.spacing));
      if (ids.has(data.zone_id)) data.zone_id=ids.get(data.zone_id);
      data.name=(row.name.slice(0,this.isZone(row)?155:195)+" copy");
      operations.push(operation("create_"+kind,id,data));
      for (const asset of this.data.assets.filter(l=>l.object_id===row.id)) operations.push(attach(asset,crypto.randomUUID(),id));
    }
    for (const edge of this.data.relationships.filter(e=>ids.has(e.source_object_id)&&ids.has(e.target_object_id))) {
      operations.push(operation("create_relationship",crypto.randomUUID(),{...canonical("relationship",edge),
        source_object_id:ids.get(edge.source_object_id),target_object_id:ids.get(edge.target_object_id)}));
    }
    this.commit(operations,[...ids.values()]);
  }
  async align(mode) {
    if (!mode || !this.editable) return;
    const selected=this.selected(), rows=this.roots(selected);
    if (rows.length<2 || rows.some(row=>this.isZone(row)|| !this.visibleRows().includes(row))) {
      this.renderBars();
      this.message("Select at least two visible objects with independent positions to align.");
      return;
    }
    await this.run(async()=>{
      const result=await this.request(this.path+"/align","POST",{object_ids:rows.map(r=>r.id),
        ...(mode==="x"||mode==="y"?{distribute_axis:mode}:{mode})});
      const deltas=result.positions.map(p=>[this.rows.get(p.object_id),sub(xy(p),xy(this.data.geometry[p.object_id].center))]);
      this.preview={translations:new Map(deltas.flatMap(([row,delta])=>[...this.descendants([row.id])].map(id=>[id,delta]))),
        guide:xy(result.positions[0])};
      this.commit(this.moveOperations(deltas));
      this.viewport.schedule();
    }, true, true);
  }
  async validate() {
    const revision=this.data.scene.current_revision, sequence=(this.validationSequence || 0)+1;
    this.validationSequence=sequence;
    this.validation=null;
    this.issueIds=new Set();
    this.inspector.renderIssues();
    try {
      const result=await this.request(this.path+"/validate","POST",{});
      if (sequence!==this.validationSequence) return;
      if (result.scene_revision!==revision) throw new Error("The scene changed elsewhere. Reload to validate the latest revision.");
      this.validation=result;
      this.issueIds=new Set(this.issues().flatMap(issue=>[issue.id,issue.other]).filter(Boolean));
    } catch (error) {
      if (sequence===this.validationSequence) this.validation={error:error.message};
    }
    this.inspector.renderIssues();
    this.viewport.schedule();
  }
  issues() {
    const v=this.validation;
    if (!v || v.error) return [];
    return [
      ...v.boundary_checks.filter(x=>x.status!=="inside").map(x=>({id:x.object_id,text:x.status==="unknown"?"Scene containment unknown":label(x.status)+" scene containment"})),
      ...v.zone_checks.filter(x=>x.status!=="inside").map(x=>({id:x.object_id||x.zone_id,text:x.reason||"Outside assigned zone"})),
      ...v.overlaps.map(x=>({id:x.first_id,other:x.second_id,text:label(x.classification)})),
      ...v.invalid_geometries.map(x=>({id:x.object_id,text:x.reason})),
    ];
  }
  async plan() {
    const body=el("div",{class:"stack"},el("p",{},"Loading plan reference…"));
    dialog("Phase 8 plan reference",body);
    try {
      const suffix=this.data.scene.event_id?"?event_id="+this.data.scene.event_id:"";
      const path="/design-intelligence/projects/"+this.project.id+"/plan";
      let [artifact]=await this.request(path+suffix);
      if (!artifact && suffix) [artifact]=await this.request(path);
      if (!artifact) {body.replaceChildren(el("p",{},"This project or event has no saved Plan artifact."));return;}
      body.replaceChildren(el("p",{},artifact.content.summary || "Conceptual layout guidance"),
        el("p",{class:"hint"},(artifact.event_id?"Event":"Project")+" plan version "+artifact.version+" · "+label(artifact.status)+(artifact.context_stale?" · context changed":"")),
        el("ul",{},(artifact.content.zones || []).map(zone=>el("li",{},el("strong",{},zone.name)," — "+zone.purpose))),
        el("p",{class:"hint"},"Use measured coordinates when placing zones from this reference."));
    } catch(error) {body.replaceChildren(el("p",{class:"error"},error.message));}
  }
  key(e) {
    const key=e.key.toLowerCase(), mod=e.ctrlKey||e.metaKey, focused=e.target;
    if (document.querySelector("dialog[open]") || (focused !== document.body && !this.root.contains(focused))) return;
    if (mod && key==="s") {e.preventDefault();this.save();return;}
    if (focused.matches("input,textarea,select,[contenteditable]")) return;
    if (!focused.matches("button") && key===" ") {e.preventDefault();this.space=true;
      window.addEventListener("keyup",()=>{this.space=false;},{once:true});return;}
    if (key==="escape") {
      if (this.drag || this.measure) this.cancel();
      else if (this.inspector.dirty) this.inspector.render();
      else {this.select(null);this.setTool("select");}
    } else if (mod&&key==="z") {e.preventDefault();if(this.editable&&!this.inspector.dirty)this.store.step(e.shiftKey?"redo":"undo");}
    else if (mod&&key==="d"&&this.selection.size) {e.preventDefault();this.duplicate();}
    else if (!mod&&!e.altKey&&key in actionKeys && (!editTools.has(actionKeys[key])||this.data.can_edit)) this.setTool(actionKeys[key]);
    else if (["+", "=","-"].includes(key)&&!mod) this.viewport.zoomBy(key==="-"?.8:1.25);
    else if (this.editable&&this.selection.size&&!mod&&["delete","backspace"].includes(key)) {e.preventDefault();this.remove();}
    else if (this.editable&&this.selection.size&&!mod&&key.startsWith("arrow")) {
      e.preventDefault();
      const amount=this.settings.spacing*(e.shiftKey?1:.1);
      const d={arrowleft:point(-amount,0),arrowright:point(amount,0),arrowup:point(0,amount),arrowdown:point(0,-amount)}[key];
      if(d)this.commit(this.moveOperations(this.roots(this.selected()).map(row=>[row,d])));
    } else if (this.editable&&this.selection.size&&!mod&&["[","]"].includes(key)) {
      const step=(key==="["?-1:1)*(e.shiftKey?1:15);
      this.commit(this.roots(this.selected()).map(row=>this.rotateOperation(row,+row.transform.rotation_deg.z+step)));
    }
  }
}
