// One metric over time: a thin line on one axis, recessive grid, crosshair and
// tooltip on hover. Which metric is chosen outside, so there is never a second axis.

import { h, s, clear, fmt } from "./dom.js";

const HEIGHT = 240;
const MARGIN = { top: 14, right: 12, bottom: 26, left: 52 };

function niceTicks(low, high, count = 4) {
  if (high <= low) high = low + 1;
  const rough = (high - low) / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((n) => n * power).find((n) => n >= rough) || rough;
  const start = Math.floor(low / step) * step;
  const ticks = [];
  for (let value = start; value < high + step * 0.999; value += step) ticks.push(Number(value.toPrecision(10)));
  return ticks;
}

export function renderChart(container, points, { format, label, invert = false }) {
  clear(container);
  const known = points.filter((point) => point.value !== null && point.value !== undefined);
  if (known.length === 0) {
    container.append(h("div", { class: "empty", text: "برای این بازه داده‌ای نیست." }));
    return;
  }
  const width = Math.max(container.clientWidth, 280);
  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;

  const values = known.map((point) => point.value);
  const low = invert ? Math.min(...values) : 0;
  const ticks = niceTicks(low, Math.max(...values));
  const domainLow = ticks[0];
  const domainHigh = ticks[ticks.length - 1];
  const y = (value) => {
    const ratio = (value - domainLow) / (domainHigh - domainLow || 1);
    return MARGIN.top + (invert ? ratio : 1 - ratio) * plotHeight;
  };
  const x = (index) => MARGIN.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);

  const svg = s("svg", {
    viewBox: `0 0 ${width} ${HEIGHT}`, role: "img",
    "aria-label": `${label}: از ${fmt.day(points[0].date)} تا ${fmt.day(points[points.length - 1].date)}`,
  });

  for (const tick of ticks) {
    svg.append(
      s("line", { x1: MARGIN.left, x2: width - MARGIN.right, y1: y(tick), y2: y(tick), stroke: "#eef0f3", "stroke-width": 1 }),
      s("text", { x: MARGIN.left - 8, y: y(tick) + 4, "text-anchor": "end", "font-size": 11, fill: "#5f6673", text: format(tick) }),
    );
  }

  const labelCount = Math.min(points.length, width < 520 ? 3 : 6);
  for (let i = 0; i < labelCount; i += 1) {
    const index = labelCount === 1 ? 0 : Math.round((i / (labelCount - 1)) * (points.length - 1));
    const anchor = index === 0 ? "start" : index === points.length - 1 ? "end" : "middle";
    svg.append(s("text", { x: x(index), y: HEIGHT - 6, "text-anchor": anchor, "font-size": 11, fill: "#5f6673", text: fmt.shortDay(points[index].date) }));
  }

  // A gap where a period has no value, rather than a line drawn across it.
  let path = "";
  let drawing = false;
  points.forEach((point, index) => {
    if (point.value === null || point.value === undefined) { drawing = false; return; }
    path += `${drawing ? "L" : "M"}${x(index).toFixed(1)} ${y(point.value).toFixed(1)}`;
    drawing = true;
  });
  svg.append(s("path", { d: path, fill: "none", stroke: "var(--chart-line)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  const cross = s("line", { y1: MARGIN.top, y2: MARGIN.top + plotHeight, stroke: "#9ca3af", "stroke-width": 1, visibility: "hidden" });
  const dot = s("circle", { r: 4.5, fill: "var(--chart-line)", stroke: "#fff", "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot);

  const tooltip = h("div", { class: "chart-tooltip hidden" });
  container.append(svg, tooltip);

  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tooltip.classList.add("hidden");
  };
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const scale = width / box.width;
    const position = (event.clientX - box.left) * scale;
    const ratio = points.length === 1 ? 0 : (position - MARGIN.left) / plotWidth;
    const index = Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1))));
    const point = points[index];
    if (point.value === null || point.value === undefined) { hide(); return; }
    const px = x(index);
    cross.setAttribute("x1", px);
    cross.setAttribute("x2", px);
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px);
    dot.setAttribute("cy", y(point.value));
    dot.setAttribute("visibility", "visible");
    tooltip.textContent = `${fmt.day(point.date)} · ${label}: ${format(point.value)}${point.partial ? " (دادهٔ ناقص)" : ""}`;
    tooltip.classList.remove("hidden");
    const tipWidth = tooltip.offsetWidth;
    const left = px / scale;
    tooltip.style.left = `${Math.max(0, Math.min(box.width - tipWidth, left - tipWidth / 2))}px`;
  });
}
