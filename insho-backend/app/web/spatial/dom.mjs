export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "on") {
      for (const [event, callback] of Object.entries(value)) node.addEventListener(event, callback);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  node.append(...children.flat().filter(child => child != null));
  return node;
}
export const replace = (node, ...children) => node.replaceChildren(...children.flat().filter(child => child != null));
export const button = (text, click, options = {}) => el("button", {type: "button", on: {click}, ...options}, text);
export const field = (label, input) => el("label", {}, label, input);
export const input = (name, value, extra = {}) => el("input", {name, value, ...extra});
export function choice(name, entries, current, attrs = {}) {
  const select = el("select", {name, ...attrs}, entries.map(([value, label]) => el("option", {value}, label)));
  select.value = current ?? "";
  return select;
}
export const number = (name, value, extra = {}) => input(name, value, {type: "number", step: "any", ...extra});

export function dialog(title, content) {
  const node = document.querySelector("#dialog");
  if (node.open) node.close();
  node.replaceChildren(el("div", {class: "stack"}, el("div", {class: "row"},
    el("h2", {}, title), button("Close", () => node.close())), content));
  node.showModal();
  return node;
}
