// Presentation math in meters. Stored shapes and final snaps come from Part 2.
export const point = (x, y) => ({x, y});
export const xy = p => point(+p.x_m, +p.y_m);
export const add = (a, b) => point(a.x + b.x, a.y + b.y);
export const sub = (a, b) => point(a.x - b.x, a.y - b.y);
export const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
export const round = n => Number(n.toFixed(6));
export const pivot = geo => point(geo.matrix[0][3], geo.matrix[1][3]);
export const screen = (camera, p) => point(camera.x + p.x * camera.scale, camera.y - p.y * camera.scale);
export const world = (camera, p) => point((p.x - camera.x) / camera.scale, (camera.y - p.y) / camera.scale);

export function zoom(camera, anchor, scale) {
  const p = world(camera, anchor);
  return {scale, x: anchor.x - p.x * scale, y: anchor.y + p.y * scale};
}

export function fit(bounds, width, height) {
  const scale = Math.max(.000001, Math.min(1500, Math.max(10, width - 92) / Math.max(.1, bounds.max_x_m - bounds.min_x_m),
    Math.max(10, height - 100) / Math.max(.1, bounds.max_y_m - bounds.min_y_m)));
  return {scale, x: width / 2 - (bounds.min_x_m + bounds.max_x_m) * scale / 2,
    y: height / 2 + (bounds.min_y_m + bounds.max_y_m) * scale / 2};
}

export function union(bounds) {
  return bounds.reduce((a, b) => a ? ({
    min_x_m: Math.min(a.min_x_m, b.min_x_m), min_y_m: Math.min(a.min_y_m, b.min_y_m),
    max_x_m: Math.max(a.max_x_m, b.max_x_m), max_y_m: Math.max(a.max_y_m, b.max_y_m),
  }) : b, null);
}

export function visibleGrid(camera, width, height, spacing) {
  const low = world(camera, point(0, height)), high = world(camera, point(width, 0));
  if (!Number.isFinite(spacing) || spacing <= 0) return [];
  while (spacing * camera.scale < 12) spacing *= 5;
  const lines = [];
  for (const [axis, min, max] of [["x", low.x, high.x], ["y", low.y, high.y]]) {
    const start = Math.ceil(min / spacing), end = Math.floor(max / spacing);
    for (let offset = 0; offset <= Math.min(end - start, 1000); offset++) {
      const i = start + offset;
      lines.push({axis, value: i * spacing, major: i % 5 === 0});
    }
  }
  return lines;
}

export function nearestGrid(value, spacing) {
  const ratio = value / spacing;
  return Math.sign(ratio) * Math.floor(Math.abs(ratio) + .5) * spacing;
}

export function previewSnap(p, spacing, anchors, limit) {
  let best = null;
  for (const a of anchors) {
    const d = distance(p, xy(a));
    if (d > limit + 1e-6 + 1e-12 * Math.max(d, limit)) continue;
    if (!best || d < best.d || (d === best.d && (a.id < best.id || (a.id === best.id && a.kind < best.kind)))) {
      best = {...a, d};
    }
  }
  if (best) return {point: xy(best), kind: best.kind};
  if (spacing) return {point: point(nearestGrid(p.x, spacing), nearestGrid(p.y, spacing)), kind: "grid"};
  return {point: p, kind: null};
}

const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a.reduce((n, v, i) => n + v * b[i], 0);
export function localDelta(matrix, delta) {
  if (!matrix) return {x: delta.x, y: delta.y, z: 0};
  const basis = [0, 1, 2].map(i => matrix.slice(0, 3).map(row => row[i]));
  const planes = basis.map((_, i) => cross(basis[(i + 1) % 3], basis[(i + 2) % 3]));
  const det = dot(basis[0], planes[0]);
  if (!det) throw new Error("The parent transform is singular.");
  const [x, y, z] = planes.map(n => dot(n, [delta.x, delta.y, 0]) / det);
  return {x, y, z};
}

export function localXY(matrix, p) {
  const [[a, b, , x], [c, d, , y]] = matrix;
  const det = a * d - b * c;
  return point((d * (p.x - x) - b * (p.y - y)) / det, (-c * (p.x - x) + a * (p.y - y)) / det);
}

export function projected(matrix, x, y) {
  return point(matrix[0][0] * x + matrix[0][1] * y + matrix[0][3], matrix[1][0] * x + matrix[1][1] * y + matrix[1][3]);
}

export function handleRotation(parent, center, start, end, increment) {
  const a = localDelta(parent, sub(start, center)), b = localDelta(parent, sub(end, center));
  const delta = (Math.atan2(b.y, b.x) - Math.atan2(a.y, a.x)) * 180 / Math.PI;
  const normalized = ((delta + 540) % 360) - 180;
  return increment ? nearestGrid(normalized, increment) : normalized;
}

export function hit(geo, p, padding) {
  const bounds = geo.bounds;
  if (!bounds || p.x < bounds.min_x_m - padding || p.x > bounds.max_x_m + padding ||
      p.y < bounds.min_y_m - padding || p.y > bounds.max_y_m + padding) return false;
  if (geo.geometry_type === "circle") return distance(p, xy(geo.center)) <= geo.radius_m;
  const vertices = (geo.vertices || []).map(xy);
  if (geo.geometry_type === "point") return distance(p, vertices[0]) <= padding;
  if (geo.geometry_type === "line_segment") {
    const [a, b] = vertices, ab = sub(b, a), ap = sub(p, a);
    const t = Math.max(0, Math.min(1, (ap.x * ab.x + ap.y * ab.y) / (ab.x ** 2 + ab.y ** 2 || 1)));
    return distance(p, point(a.x + t * ab.x, a.y + t * ab.y)) <= padding;
  }
  let inside = false;
  for (let i = 0; i < vertices.length; i++) {
    const a = vertices[i], b = vertices[(i + 1) % vertices.length];
    if ((a.y > p.y) !== (b.y > p.y) && p.x < a.x + (b.x - a.x) * (p.y - a.y) / (b.y - a.y)) inside = !inside;
  }
  return inside;
}
