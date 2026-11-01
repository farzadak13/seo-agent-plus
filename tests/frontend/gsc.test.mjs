// The performance page: what is on screen always matches what its buttons act on.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, findAll, settle, type } from "./fake_dom.mjs";
import { gscPage } from "../../frontend/js/pages/gsc.js";
import { fmt } from "../../frontend/js/dom.js";

const SITE = { site_id: "site-1", gsc_configured: true, gsc_property_url: "sc-domain:example.test" };
const END = "2026-10-29";
const ANALYSE = "تحلیل و پیشنهاد عنوان";
const PAGE_URL = "https://example.test/page";

const row = (key) => ({
  key, current: { clicks: 1, impressions: 2, ctr: 0.5, position: 3 }, previous: null, search_volume: null,
});

function chartFor(url) {
  // The total says which range this answer is for.
  const days = url.searchParams.has("start") ? (new Date(END) - new Date(url.searchParams.get("start"))) / 86400000 + 1 : 28;
  return {
    end: END, interval: "day", series: [], days_in_range: days, days_covered: days,
    totals: { clicks: days, impressions: 0, ctr: 0, position: 0 },
  };
}

let server;

beforeEach(() => {
  server = { requests: [], posts: [], fail: () => false, hold: () => null, table: {} };
  globalThis.fetch = async (url, init) => {
    if (init.method === "POST") {
      server.posts.push(JSON.parse(init.body));
      return answer(202, { run_id: "run-1" });
    }
    if (url.pathname.endsWith("/sync")) return answer(200, { state: "up_to_date" });
    server.requests.push(url);
    const held = server.hold(url);
    if (held) await held;
    if (server.fail(url)) return answer(500, { detail: "no" });
    if (url.pathname.endsWith("/performance")) return answer(200, chartFor(url));
    const tab = url.pathname.endsWith("/pages") ? "pages" : "queries";
    return answer(200, {
      rows: tab === "pages" ? [row(PAGE_URL)] : [row(url.searchParams.get("page") ? "word on page" : "word")],
      next_offset: 50, previous_days_covered: 28, previous_days_in_range: 28, ...server.table,
    });
  };
});

async function open() {
  const container = new FakeElement("main");
  await gscPage(container, { site: SITE, alive: () => true, refreshCount: () => {} });
  return container;
}

const alerts = (container) => findAll(container, (element) => element.attributes.role === "alert" && element.className.includes("notice"));
const status = (container) => findAll(container, (element) => element.attributes.role === "status")[0];
const pressed = (container) => findAll(container, (element) => element.attributes["aria-pressed"] === "true" && element.className.includes("filter-btn")).map((element) => element.textContent);
const firstKpi = (container) => findAll(container, (element) => element.className === "kpi-value")[0].textContent;

async function openPage(container) {
  button(container, "صفحه‌ها").click();
  await settle();
  button(container, "/page").click();
  await settle();
}

test("a failed first load offers a way to try again, which works", async () => {
  server.fail = () => true;
  const container = await open();
  assert.equal(alerts(container).length, 1);

  server.fail = () => false;
  button(container, "تلاش دوباره").click();
  await settle();

  assert.equal(alerts(container).length, 0);
  assert.ok(container.textContent.includes("word"));
  assert.equal(firstKpi(container), fmt.int(28));
});

test("a failed tab change keeps the rows, and analysing still names their page", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const container = await open();
  await openPage(container);

  server.fail = () => true;
  button(container, "صفحه‌ها").click();
  await settle();
  assert.equal(alerts(container).length, 1);
  assert.ok(container.textContent.includes("word on page"));

  button(container, ANALYSE).click();
  await settle();
  assert.equal(server.posts.length, 1);
  assert.equal(server.posts[0].normalized_url, PAGE_URL);
});

test("analysing still names the page while a tab change is on its way", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const container = await open();
  await openPage(container);

  let release;
  server.hold = () => new Promise((resolve) => { release = resolve; });
  button(container, "همهٔ صفحه‌ها").click();
  await settle();
  server.hold = () => null;
  button(container, ANALYSE).click();
  await settle();

  assert.equal(server.posts[0].normalized_url, PAGE_URL);
  release();
});

test("a failed load does not write over what an analysis has to say", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const container = await open();
  await openPage(container);
  button(container, ANALYSE).click();
  await settle();
  const said = status(container).textContent;
  assert.match(said, /در حال تحلیل/);

  server.fail = () => true;
  button(container, "کلیک‌ها ▼").click();
  await settle();

  assert.equal(status(container).textContent, said);
  assert.equal(alerts(container).length, 1);
});

test("a later load that works takes the error away", async () => {
  const container = await open();
  server.fail = () => true;
  button(container, "کلیک‌ها ▼").click();
  await settle();
  assert.equal(alerts(container).length, 1);

  server.fail = () => false;
  button(container, "کلیک‌ها ▼").click();
  await settle();
  assert.equal(alerts(container).length, 0);
});

test("after a failed sort, more rows are more of what is on screen", async () => {
  const container = await open();
  server.fail = () => true;
  button(container, "نمایش").click();
  await settle();

  server.fail = () => false;
  server.requests = [];
  button(container, "نمایش بیشتر").click();
  await settle();

  const [url] = server.requests;
  assert.equal(url.searchParams.get("sort"), "clicks");
  assert.equal(url.searchParams.get("offset"), "50");
});

test("a failed range falls back to the range on screen", async () => {
  const container = await open();
  server.fail = () => true;
  button(container, "۷ روز").click();
  assert.ok(pressed(container).includes("۷ روز"), "shown at once, as the answer can take a while");
  await settle();

  assert.ok(pressed(container).includes("۲۸ روز"));
  assert.ok(!pressed(container).includes("۷ روز"));
  assert.equal(firstKpi(container), fmt.int(28));
});

test("a late answer for an older range does not replace the newer one", async () => {
  const container = await open();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  server.hold = (url) => (url.pathname.endsWith("/performance") && url.searchParams.get("start") === "2026-10-23" ? held : null);

  button(container, "۷ روز").click();
  await settle();
  button(container, "۳ ماه").click();
  await settle();
  release();
  await settle();

  assert.equal(firstKpi(container), fmt.int(90));
  assert.ok(pressed(container).includes("۳ ماه"));
});

test("a range chosen while the page is still opening is the one the chart shows", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  server.hold = () => held;
  const container = new FakeElement("main");
  const opening = gscPage(container, { site: SITE, alive: () => true, refreshCount: () => {} });
  await settle();
  server.hold = () => null;

  button(container, "۳ ماه").click();
  await settle();
  release();
  await opening;
  await settle();

  assert.equal(firstKpi(container), fmt.int(90));
  assert.ok(pressed(container).includes("۳ ماه"));
});

test("a range chosen after a failed first load is the one the chart shows", async () => {
  server.fail = () => true;
  const container = await open();

  server.fail = () => false;
  button(container, "۷ روز").click();
  await settle();

  assert.equal(firstKpi(container), fmt.int(7));
  assert.equal(alerts(container).length, 0);
});

test("the usual first load asks for the chart once", async () => {
  await open();
  assert.equal(server.requests.filter((url) => url.pathname.endsWith("/performance")).length, 1);
});

test("what was asked for by an overtaken click is not lost", async () => {
  const container = await open();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  server.hold = () => held;
  button(container, "۷ روز").click();
  await settle();
  server.hold = () => null;
  button(container, "موبایل").click();
  await settle();
  release();
  await settle();

  const last = server.requests[server.requests.length - 1];
  assert.equal(last.searchParams.get("device"), "MOBILE");
  assert.equal(last.searchParams.get("start"), "2026-10-23");
  assert.equal(firstKpi(container), fmt.int(7));
});

test("a search is sent once typing stops, and failing leaves the words in the box", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const container = await open();
  const box = findAll(container, (element) => element.type === "search")[0];
  server.requests = [];
  type(box, "کف");
  type(box, "کفش");
  assert.equal(server.requests.length, 0);

  server.fail = () => true;
  t.mock.timers.tick(350);
  await settle();

  assert.equal(server.requests.length, 1);
  assert.equal(server.requests[0].searchParams.get("contains"), "کفش");
  assert.equal(box.value, "کفش");
  assert.equal(alerts(container).length, 1);
});
