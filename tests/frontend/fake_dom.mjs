// Just enough of a browser for the pages to build and be clicked, so that their
// logic can be tested without a dependency: the dashboard has no build step and
// no packages, and its tests should not bring any.

class FakeNode {}

class FakeText extends FakeNode {
  constructor(text) {
    super();
    this.textContent = String(text);
  }
}

export class FakeElement extends FakeNode {
  constructor(tagName) {
    super();
    this.tagName = tagName;
    this.childNodes = [];
    this.attributes = {};
    this.listeners = {};
    this.focused = false;
    // Set as properties by h(), which checks `name in element`.
    Object.assign(this, {
      value: "", checked: false, disabled: false, type: "", name: "", id: "", href: "",
      target: "", rel: "", title: "", placeholder: "", maxLength: 0, colSpan: 1,
    });
    const classes = () => (this.attributes.class || "").split(/\s+/).filter(Boolean);
    this.classList = {
      contains: (name) => classes().includes(name),
      add: (name) => { this.attributes.class = [...new Set([...classes(), name])].join(" "); },
      remove: (name) => { this.attributes.class = classes().filter((item) => item !== name).join(" "); },
      toggle: (name, on) => { if (on) this.classList.add(name); else this.classList.remove(name); },
    };
  }

  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get textContent() { return this.childNodes.map((child) => child.textContent).join(""); }
  set textContent(value) { this.childNodes = [new FakeText(value)]; }

  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  append(...items) {
    for (const item of items) this.childNodes.push(item instanceof FakeNode ? item : new FakeText(item));
  }
  replaceChildren(...items) {
    this.childNodes = [];
    this.append(...items);
  }
  focus() { this.focused = true; }
  setSelectionRange() {}
  dispatch(type) {
    for (const listener of this.listeners[type] || []) listener({ target: this });
  }
  click() { this.dispatch("click"); }
}

globalThis.Node = FakeNode;
globalThis.SVGElement = class extends FakeElement {};
globalThis.window = { location: { origin: "http://localhost" } };
globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
  createElementNS: (_namespace, tagName) => new globalThis.SVGElement(tagName),
  createTextNode: (text) => new FakeText(text),
  getElementById: () => null,
};
globalThis.ResizeObserver = class {
  observe() {}
  disconnect() {}
};

export function findAll(root, test) {
  const found = [];
  for (const child of root.childNodes || []) {
    if (child instanceof FakeElement && test(child)) found.push(child);
    found.push(...findAll(child, test));
  }
  return found;
}

export function button(root, text) {
  const [found] = findAll(root, (element) => element.tagName === "button" && element.textContent === text);
  if (!found) throw new Error(`no button "${text}"`);
  return found;
}

// A real browser keeps one radio of a group checked; this one is told to.
export function choose(card, value) {
  const radios = findAll(card, (element) => element.type === "radio");
  for (const radio of radios) radio.checked = radio.value === value;
  radios.find((radio) => radio.value === value).dispatch("change");
}

export function type(input, text) {
  input.value = text;
  input.dispatch("input");
}

// Let every answered request and whatever it set off run to the end.
export async function settle() {
  for (let round = 0; round < 12; round += 1) await new Promise((resolve) => { setImmediate(resolve); });
}

export function answer(status, data) {
  return { status, ok: status < 400, json: async () => data };
}
