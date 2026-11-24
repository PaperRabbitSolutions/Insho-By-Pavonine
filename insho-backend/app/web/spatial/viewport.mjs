import {add, fit, pivot, point, projected, screen, sub, union, visibleGrid, world, xy, zoom, hit} from "./geometry.mjs";

const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
const line = (a, b, color, width = 1, dash = "") => svg("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y,
  stroke: color, "stroke-width": width, "stroke-dasharray": dash, "vector-effect": "non-scaling-stroke"});
const poly = (points, closed = true) => points.length ? "M" + points.map(p => p.x + "," + p.y).join("L") + (closed ? "Z" : "") : "";
const colors = {stage: "#bb885b", platform: "#a78862", mandap: "#ae795e", chair: "#698578", table: "#8e8971",
  seating_block: "#648c78", floral_installation: "#936c82", lighting_element: "#a68c47", dance_floor: "#778ca3"};
export const color = (row, zone = false) => /^#[\da-f]{6}$/i.test(row.appearance?.color)
  ? row.appearance.color : zone ? "#728e76" : colors[row.category] || "#63877a";

export class Viewport {
  constructor(svgNode, editor) {
    this.node = svgNode;
    this.editor = editor;
    this.width = this.height = 1;
    this.camera = {x: 0, y: 0, scale: 25};
    this.frame = 0;
    this.shapeCache = new Map();
    this.rendered = [];
    this.grid = svg("g", {"pointer-events": "none"});
    this.scene = svg("g", {"pointer-events": "none"});
    this.objects = svg("g", {"pointer-events": "none"});
    this.overlay = svg("g");
    this.node.replaceChildren(this.scene, this.grid, this.objects, this.overlay);
    this.resize = new ResizeObserver(([entry]) => {
      const {width, height} = entry.contentRect;
      if (!width || !height) return;
      this.camera.x += (width - this.width) / 2;
      this.camera.y += (height - this.height) / 2;
      this.width = width;
      this.height = height;
      if (!this.initialScale) this.fit("scene");
      this.schedule();
    });
    this.resize.observe(svgNode);
    svgNode.addEventListener("pointerdown", e => this.editor.pointer(e));
    // Continue a gesture even if the browser releases capture during a redraw.
    for (const event of ["pointermove", "pointerup", "pointercancel"]) {
      document.addEventListener(event, e => {
        if (this.editor.drag) this.editor.pointer(e);
      });
    }
    window.addEventListener("blur", () => {
      if (this.editor.drag) this.editor.cancel();
    });
    svgNode.addEventListener("wheel", e => {
      e.preventDefault();
      this.zoomBy(Math.exp(-Math.sign(e.deltaY) * .16), this.pointer(e));
    }, {passive: false});
  }
  pointer(e) {
    const rect = this.node.getBoundingClientRect();
    return point(e.clientX - rect.left, e.clientY - rect.top);
  }
  bounds() {
    const d = this.editor.data.scene.dimensions;
    return {min_x_m: 0, min_y_m: 0, max_x_m: +d.width_m, max_y_m: +d.depth_m};
  }
  fit(mode) {
    const e = this.editor;
    const content = union(e.visibleRows().filter(row => mode !== "selection" || e.selection.has(row.id))
      .map(row => e.data.geometry[row.id]?.bounds).filter(Boolean));
    this.camera = fit(mode === "scene" ? this.bounds() : content || this.bounds(), this.width, this.height);
    if (mode === "scene" && this.width > 1) this.initialScale ??= this.camera.scale;
    this.schedule();
  }
  center() {
    const d = this.bounds();
    this.camera.x = (this.width - d.max_x_m * this.camera.scale) / 2;
    this.camera.y = (this.height + d.max_y_m * this.camera.scale) / 2;
    this.schedule();
  }
  zoomBy(factor, anchor = point(this.width / 2, this.height / 2)) {
    const scale = Math.max(.000001, Math.min(2000, this.camera.scale * factor));
    this.camera = zoom(this.camera, anchor, scale);
    this.schedule();
  }
  getHits(p) {
    return [...this.editor.visibleRows()].reverse().filter(row => hit(this.editor.data.geometry[row.id] || {}, p, 7 / this.camera.scale));
  }
  schedule() {
    if (!this.frame) this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }
  draw() {
    const e = this.editor, {width, height, camera} = this, s = camera.scale;
    const transform = "translate(" + camera.x + " " + camera.y + ") scale(" + s + " " + -s + ")";
    this.objects.setAttribute("transform", transform);
    this.scene.setAttribute("transform", transform);
    const bounds = this.bounds();
    this.scene.replaceChildren(svg("rect", {width: bounds.max_x_m, height: bounds.max_y_m, fill: "#fbfcf8",
      stroke: "#778576", "stroke-width": 1, "vector-effect": "non-scaling-stroke"}));
    const grid = {major: [], minor: []};
    if (e.settings.grid) {
      for (const {axis, value, major} of visibleGrid(camera, width, height, e.settings.spacing)) {
        const key = major ? "major" : "minor";
        if (axis === "x") {
          const x = screen(camera, point(value, 0)).x;
          grid[key].push("M" + x + ",0V" + height);
        } else {
          const y = screen(camera, point(0, value)).y;
          grid[key].push("M0," + y + "H" + width);
        }
      }
    }
    this.grid.replaceChildren(...Object.entries(grid).map(([key, commands]) => svg("path", {
      d: commands.join(""), fill: "none", stroke: key === "major" ? "#c6d3c6" : "#dbe4d9"})));
    const low = world(camera, point(0, height)), high = world(camera, point(width, 0));
    const labels = [];
    const shapes = [];
    if (this.snapshot !== e.data) {this.shapeCache.clear();this.snapshot = e.data;}
    for (const row of e.visibleRows()) {
      const geo = e.data.geometry[row.id];
      if (!geo?.bounds) continue;
      const delta = e.preview?.translations?.get(row.id);
      const special = e.preview?.shapes?.get(row.id);
      const b = geo.bounds;
      if (!special && (b.max_x_m + (delta?.x || 0) < low.x || b.min_x_m + (delta?.x || 0) > high.x ||
          b.max_y_m + (delta?.y || 0) < low.y || b.min_y_m + (delta?.y || 0) > high.y)) continue;
      const zone = e.isZone(row), selected = e.selection.has(row.id);
      const outline = selected ? "#155a42" : e.issueIds.has(row.id) ? "#a13329" : color(row, zone);
      const attrs = {stroke: outline, "stroke-width": selected ? 2 : 1.25, "vector-effect": "non-scaling-stroke",
        fill: color(row, zone), "fill-opacity": zone ? .14 : .31, "data-shape": row.id};
      if (zone) attrs["stroke-dasharray"] = "5 3";
      if (delta) attrs.transform = "translate(" + delta.x + " " + delta.y + ")";
      let cached = this.shapeCache.get(row.id);
      const key = [outline, selected, geo.geometry_type === "point" ? s : null];
      if (!cached || cached.key.some((value, i) => value !== key[i])) {
        const shapeAttrs = {...attrs};
        delete shapeAttrs.transform;
        let node;
        if (geo.geometry_type === "circle") node = svg("circle", {...shapeAttrs, cx: geo.center.x_m, cy: geo.center.y_m, r: geo.radius_m});
        else if (geo.geometry_type === "point") node = svg("circle", {...shapeAttrs, cx: geo.center.x_m, cy: geo.center.y_m, r: 4 / s});
        else node = svg("path", {...shapeAttrs, d: poly(geo.vertices.map(xy), geo.geometry_type !== "line_segment")});
        cached = {key, node};
        this.shapeCache.set(row.id, cached);
      }
      if (special) shapes.push(svg("path", {...attrs, d: poly(special, geo.geometry_type !== "line_segment")}));
      else {
        if (delta) cached.node.setAttribute("transform", attrs.transform);
        else if (cached.node.hasAttribute("transform")) cached.node.removeAttribute("transform");
        shapes.push(cached.node);
      }
      const labelPoint = add(xy(geo.center), delta || point(0, 0));
      if (selected || (b.width_m * s > 85 && b.depth_m * s > 20)) {
        const p = screen(camera, labelPoint);
        labels.push(svg("text", {x: p.x, y: p.y + 4, "text-anchor": "middle", fill: "#254439",
          "font-size": 11, "pointer-events": "none"}, row.name.length > 28 ? row.name.slice(0, 27) + "…" : row.name));
      }
    }
    const overlays = [...labels];
    if (e.preview?.draft) {
      const draft = e.preview.draft;
      shapes.push(svg("path", {d: poly(draft), fill: "#377c5b45", stroke: "#205b44", "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke"}));
    }
    if (e.preview?.guide) {
      const p = screen(camera, e.preview.guide);
      overlays.push(line(point(p.x, 0), point(p.x, height), "#537a64", 1, "4 4"),
        line(point(0, p.y), point(width, p.y), "#537a64", 1, "4 4"));
    }
    if (e.measure?.end) {
      const a = screen(camera, e.measure.start), b = screen(camera, e.measure.end);
      overlays.push(line(a, b, "#205b44", 2, "5 3"),
        svg("text", {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 9, fill: "#205b44", "text-anchor": "middle"},
          e.format(Math.hypot(e.measure.end.x - e.measure.start.x, e.measure.end.y - e.measure.start.y))));
    }
    if (!e.drag && !e.preview && e.selection.size === 1) this.handles(overlays);
    if (shapes.length !== this.rendered.length || shapes.some((node, i) => node !== this.rendered[i])) {
      this.objects.replaceChildren(...shapes);
      this.rendered = shapes;
    }
    this.overlay.replaceChildren(...overlays);
    e.zoomText.textContent = Math.round(100 * s / (this.initialScale || s)) + "%";
    const displaySpan = 86 / s / e.factor;
    const power = 10 ** Math.floor(Math.log10(displaySpan));
    const nice = ([5, 2, 1].find(n => n * power <= displaySpan) || 1) * power;
    e.scaleText.textContent = e.format(nice * e.factor);
    const span = nice * e.factor * s;
    this.overlay.append(line(point(width - 12 - span, height - 48), point(width - 12, height - 48), "#43564a", 1.5));
  }
  handles(output) {
    const e = this.editor, row = e.selected()[0], geo = e.data.geometry[row.id];
    if (!geo?.bounds) return;
    const c = this.camera, b = geo.bounds;
    const lo = screen(c, point(b.min_x_m, b.min_y_m)), hi = screen(c, point(b.max_x_m, b.max_y_m));
    output.push(svg("rect", {x: lo.x, y: hi.y, width: hi.x - lo.x, height: lo.y - hi.y, stroke: "#247054",
      "stroke-dasharray": "3 4", fill: "none", "pointer-events": "none"}));
    if (row.dimensions) {
      output.push(svg("text", {x: (lo.x + hi.x) / 2, y: lo.y + 16, "text-anchor": "middle", fill: "#205b44",
        "font-size": 11, "pointer-events": "none"}, e.format(row.dimensions.width_m) + " × " + e.format(row.dimensions.depth_m)));
    }
    if (!e.canHandle(row)) return;
    const {width_m: w, depth_m: d} = row.dimensions;
    const center = screen(c, pivot(geo)), top = screen(c, projected(geo.matrix, 0, +d / 2));
    const dir = sub(top, center), length = Math.hypot(dir.x, dir.y) || 1;
    const rotation = add(top, point(dir.x / length * 20, dir.y / length * 20));
    output.push(line(top, rotation, "#205b44"), svg("circle", {cx: rotation.x, cy: rotation.y, r: 6, fill: "#fff",
      stroke: "#205b44", "stroke-width": 2, "data-handle": "rotate"}));
    for (const [x, y] of [[1, 1], [-1, 1], [-1, -1], [1, -1]]) {
      const pos = screen(c, projected(geo.matrix, x * w / 2, y * d / 2));
      output.push(svg("rect", {x: pos.x - 5, y: pos.y - 5, width: 10, height: 10, fill: "#fff",
        stroke: "#205b44", "stroke-width": 1.5, "data-handle": "resize", "data-x": x, "data-y": y}));
    }
  }
}
