// Building elements and formatting values. Text goes in through textContent
// only: nothing from the API or a customer's site is ever parsed as HTML.

const SVG_NS = "http://www.w3.org/2000/svg";

function build(element, attrs, children) {
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (name === "class") element.setAttribute("class", value);
    else if (name === "text") element.textContent = value;
    else if (name in element && name !== "list" && !(element instanceof SVGElement)) element[name] = value;
    else element.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

export function h(tag, attrs, ...children) {
  return build(document.createElement(tag), attrs, children);
}

export function s(tag, attrs, ...children) {
  return build(document.createElementNS(SVG_NS, tag), attrs, children);
}

export function clear(element) {
  element.replaceChildren();
  return element;
}

const integer = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("fa-IR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dayFormat = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const shortDay = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "short", timeZone: "UTC" });
const moment = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
const localDay = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "long" });

export const fmt = {
  int: (value) => (value === null || value === undefined ? "—" : integer.format(value)),
  pct: (ratio) => (ratio === null || ratio === undefined ? "—" : `${decimal.format(ratio * 100)}٪`),
  pos: (value) => (value === null || value === undefined ? "—" : decimal.format(value)),
  // A Search Console day ("2026-10-21") is a calendar day, not a moment.
  day: (iso) => (iso ? dayFormat.format(new Date(`${iso}T00:00:00Z`)) : "—"),
  shortDay: (iso) => (iso ? shortDay.format(new Date(`${iso}T00:00:00Z`)) : "—"),
  moment: (iso) => (iso ? moment.format(new Date(iso)) : "—"),
  localDay: (when) => localDay.format(when),
  signedPct: (ratio) => {
    const value = decimal.format(Math.abs(ratio) * 100);
    // Isolated left-to-right, or the sign lands on the far side of the number.
    return `⁦${ratio > 0 ? "+" : ratio < 0 ? "−" : ""}${value}٪⁩`;
  },
};

// "https://example.com/%D8%A7..." as a person reads it.
export function readableUrl(url) {
  try {
    const parsed = new URL(url);
    const path = decodeURI(parsed.pathname + parsed.search);
    return path === "/" ? parsed.host : path;
  } catch {
    return url;
  }
}

export function isoDaysBefore(iso, days) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

export function storedGet(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

export function storedSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private window: only a convenience */ }
}
