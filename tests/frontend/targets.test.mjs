// Target words: what a new site's pages should be found by, and where they stand.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, findAll, settle, type } from "./fake_dom.mjs";
import { targetsPanel } from "../../frontend/js/pages/targets.js";

const SITE = { site_id: "site-1", base_url: "https://shop.test/" };
const BASE = "/v1/sites/site-1/target-keywords";

function word(keyword, extra = {}) {
  return { keyword, url: "https://shop.test/a", status: "suggested", source: "model", intent: "commercial", reason: "دلیل", checks: [], ...extra };
}

function set(extra = {}) {
  return {
    site_id: "site-1", description: "", seeds: [], keywords: [], suggestion_status: "idle", suggestion_error: null,
    pages: [{ url: "https://shop.test/", title: "خانه" }, { url: "https://shop.test/a", title: "کفش ورزشی" }],
    ...extra,
  };
}

let server;

beforeEach(() => {
  server = { state: set(), calls: [], reply: {}, runs: {} };
  globalThis.fetch = async (url, init) => {
    const path = url.pathname;
    if (path.startsWith("/v1/runs/")) return answer(200, server.runs[path.split("/").at(-1)]);
    if (init.method !== "GET") {
      const body = init.body ? JSON.parse(init.body) : null;
      server.calls.push([init.method, path, body]);
      const reply = server.reply[path];
      if (reply) return reply(body);
      return answer(200, server.state);
    }
    // Later answers are the list alone, without the pages.
    const { pages, ...rest } = server.state;
    return answer(200, server.calls.length ? rest : server.state);
  };
});

async function open(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const holder = new FakeElement("div");
  holder.append(targetsPanel({ site: SITE, alive: () => true }));
  await settle();
  return holder;
}

test("the brief is saved, then words are asked for and shown when they come", async (t) => {
  const holder = await open(t);
  const [description] = findAll(holder, (element) => element.tagName === "textarea");
  type(description, "فروشگاه کفش ورزشی");
  server.reply[`${BASE}/suggest`] = () => {
    server.state = set({ suggestion_status: "running" });
    return answer(202, server.state);
  };

  button(holder, "پیشنهاد کلمه برای صفحه‌ها").click();
  await settle();
  assert.deepEqual(server.calls.map(([method, path]) => [method, path]), [["PUT", `${BASE}/brief`], ["POST", `${BASE}/suggest`]]);
  assert.equal(server.calls[0][2].description, "فروشگاه کفش ورزشی");
  assert.ok(holder.textContent.includes("در حال پیشنهاد"));

  server.state = set({ keywords: [word("کفش ورزشی")] });
  t.mock.timers.tick(2000);
  await settle();
  assert.ok(holder.textContent.includes("کفش ورزشی"));
  assert.ok(button(holder, "نگه داشتن") && button(holder, "لازم نیست"));
  assert.ok(!holder.textContent.includes("null"), "nothing missing is written out");
});

test("keeping a word says so to the server and shows where it stands", async (t) => {
  server.state = set({ keywords: [word("کفش ورزشی")] });
  const holder = await open(t);
  server.reply[`${BASE}/status`] = () => answer(200, set({ keywords: [word("کفش ورزشی", { status: "confirmed" })] }));

  button(holder, "نگه داشتن").click();
  await settle();
  assert.deepEqual(server.calls[0][2], { keyword: "کفش ورزشی", status: "confirmed" });
  assert.ok(holder.textContent.includes("هنوز بررسی نشده"));
  assert.ok(button(holder, "پیشنهاد عنوان"));
});

test("a week's change in position is put in words", async (t) => {
  const checks = [{ checked_at: "2026-10-25T00:00:00Z", position: 9 }, { checked_at: "2026-11-01T00:00:00Z", position: 4 }];
  server.state = set({ keywords: [word("کفش ورزشی", { status: "confirmed", checks }), word("کفش اصل", { status: "confirmed", checks: [{ position: null }] })] });
  const holder = await open(t);
  assert.ok(holder.textContent.includes("جایگاه ۴"));
  assert.ok(holder.textContent.includes("۵ رتبه بالاتر از هفتهٔ قبل"));
  assert.ok(holder.textContent.includes("در ۱۰ نتیجهٔ اول نیست"));
});

test("a title asked for is followed until it waits in the inbox", async (t) => {
  server.state = set({ keywords: [word("کفش ورزشی", { status: "confirmed" })] });
  const holder = await open(t);
  server.reply[`${BASE}/title`] = () => answer(202, { run_id: "run-1", status: "queued" });
  server.runs["run-1"] = { status: "running" };

  button(holder, "پیشنهاد عنوان").click();
  await settle();
  assert.ok(holder.textContent.includes("در حال مقایسه با نتایج گوگل"));

  server.runs["run-1"] = { status: "completed", result: { action_id: "candidate-run-1", title_proposal_status: "completed" } };
  t.mock.timers.tick(2000);
  await settle();
  const [link] = findAll(holder, (element) => element.tagName === "a" && element.href.startsWith("#/inbox/"));
  assert.equal(link.href, "#/inbox/candidate-run-1");
});

test("a site not connected is told how to go on, in Persian", async (t) => {
  server.state = set({ keywords: [word("کفش ورزشی", { status: "confirmed" })] });
  const holder = await open(t);
  server.reply[`${BASE}/title`] = () => answer(409, { detail: "Connect the site first, so its page can be read and changed." });

  button(holder, "پیشنهاد عنوان").click();
  await settle();
  assert.ok(holder.textContent.includes("اول اتصال سایت را در تنظیمات پروژه کامل کنید."));
  assert.equal(button(holder, "پیشنهاد عنوان").disabled, false);
});

test("a word of the customer's own goes with the page chosen for it", async (t) => {
  const holder = await open(t);
  const [input] = findAll(holder, (element) => element.tagName === "input" && element.placeholder === "کلمهٔ دلخواه");
  const [page] = findAll(holder, (element) => element.tagName === "select");
  assert.equal(findAll(page, (element) => element.tagName === "option").length, 2);
  type(input, "کفش مردانه");
  page.value = "https://shop.test/a";

  button(holder, "افزودن").click();
  await settle();
  assert.deepEqual(server.calls[0], ["POST", BASE, { keyword: "کفش مردانه", url: "https://shop.test/a" }]);
  assert.equal(findAll(holder, (element) => element.tagName === "option").length, 2, "the pages stay after a later answer");
});

test("a panel opened while words are on their way follows them", async (t) => {
  server.state = set({ suggestion_status: "running" });
  const holder = await open(t);
  assert.ok(holder.textContent.includes("در حال پیشنهاد"));

  server.state = set({ keywords: [word("کفش ورزشی")] });
  t.mock.timers.tick(2000);
  await settle();
  assert.ok(holder.textContent.includes("کفش ورزشی"));
  assert.ok(!holder.textContent.includes("در حال پیشنهاد"));
});

test("a title held back says why, and does not promise one in the inbox", async (t) => {
  server.state = set({ keywords: [word("کفش ورزشی", { status: "confirmed" })] });
  const holder = await open(t);
  server.reply[`${BASE}/title`] = () => answer(202, { run_id: "run-1", status: "queued" });
  server.runs["run-1"] = { status: "completed", result: { title_proposal_status: "blocked", title_proposal_reasons: ["serp_decision_not_pass", "serp_unavailable:SERPBudgetExceeded"] } };

  button(holder, "پیشنهاد عنوان").click();
  await settle();
  t.mock.timers.tick(2000);
  await settle();
  assert.ok(holder.textContent.includes("الان به نتایج گوگل دسترسی نداشتیم"));
  assert.ok(!holder.textContent.includes("صندوق تأیید"));
});

test("what is being typed stays while the panel follows a suggestion", async (t) => {
  server.state = set({ suggestion_status: "running" });
  const holder = await open(t);
  const [description] = findAll(holder, (element) => element.tagName === "textarea");
  const [input] = findAll(holder, (element) => element.tagName === "input" && element.placeholder === "کلمهٔ دلخواه");
  type(description, "فروشگاه کفش");
  type(input, "کفش مردانه");

  t.mock.timers.tick(2000);
  await settle();
  const [after] = findAll(holder, (element) => element.tagName === "textarea");
  const [word] = findAll(holder, (element) => element.tagName === "input" && element.placeholder === "کلمهٔ دلخواه");
  assert.equal(after.value, "فروشگاه کفش");
  assert.equal(word.value, "کفش مردانه");
});


test("each word says where the named competitors and the first three stand", async (t) => {
  const checks = [
    { checked_at: "2026-10-25T00:00:00Z", position: 6, watched: ["b.example"], rivals: [{ host: "b.example", position: 5 }], leaders: [] },
    {
      checked_at: "2026-11-01T00:00:00Z", position: 4,
      watched: ["b.example", "gone.example"], rivals: [{ host: "b.example", position: 2 }],
      leaders: [{ host: "a.example", position: 1 }, { host: "b.example", position: 2 }, { host: "c.example", position: 3 }],
    },
  ];
  server.state = set({ competitors: ["b.example", "gone.example"], competitors_allowed: true, keywords: [word("کفش ورزشی", { status: "confirmed", checks })] });
  const holder = await open(t);
  assert.ok(holder.textContent.includes("رقیب‌ها: b.example جایگاه ۲ (بالاتر آمد) · gone.example در ۱۰ نتیجهٔ اول نیست"));
  assert.ok(holder.textContent.includes("سایت‌های بالای نتایج: a.example (۱)، b.example (۲)، c.example (۳)"));
});

test("competitors are saved as typed, up to three", async (t) => {
  server.state = set({ competitors_allowed: true, keywords: [word("کفش ورزشی", { status: "confirmed" })] });
  const holder = await open(t);
  const field = findAll(holder, (node) => node.attributes["aria-label"] === "سایت رقیب‌ها")[0];
  type(field, "digikala.com، torob.com, a.ir b.ir");
  button(holder, "ذخیرهٔ رقیب‌ها").click();
  await settle();
  assert.equal(server.calls.length, 0, "four are not sent");
  assert.ok(holder.textContent.includes("فقط سه رقیب را می‌شود دنبال کرد"));
  type(field, "digikala.com، torob.com, a.ir");
  button(holder, "ذخیرهٔ رقیب‌ها").click();
  await settle();
  assert.deepEqual(server.calls.at(-1), ["PUT", `${BASE}/competitors`, { competitors: ["digikala.com", "torob.com", "a.ir"] }]);
  assert.ok(holder.textContent.includes("رقیب‌ها ذخیره شد"));
});

test("without the gold plan the panel says where competitors are", async (t) => {
  server.state = set({ competitors_allowed: false, keywords: [word("کفش ورزشی", { status: "confirmed" })] });
  const holder = await open(t);
  assert.ok(holder.textContent.includes("دیدن رتبهٔ رقیب‌ها کنار رتبهٔ خودتان مخصوص اشتراک طلایی است."));
  assert.equal(findAll(holder, (node) => node.tagName === "button" && node.textContent === "ذخیرهٔ رقیب‌ها").length, 0);
});

test("a plan without competitors does not see what was kept under one that had them", async (t) => {
  const checks = [{ checked_at: "2026-11-01T00:00:00Z", position: 4, rivals: [{ host: "b.example", position: 2 }], leaders: [{ host: "a.example", position: 1 }] }];
  server.state = set({ competitors: ["b.example"], competitors_allowed: false, keywords: [word("کفش ورزشی", { status: "confirmed", checks })] });
  const holder = await open(t);
  assert.ok(!holder.textContent.includes("رقیب‌ها:"));
  assert.ok(!holder.textContent.includes("سایت‌های بالای نتایج"));
});

test("a competitor named since the last look is not yet looked for, not out of the first ten", async (t) => {
  const checks = [{ checked_at: "2026-11-01T00:00:00Z", position: 4, watched: ["b.example"], rivals: [], leaders: [] }];
  server.state = set({ competitors: ["b.example", "new.example"], competitors_allowed: true, keywords: [word("کفش ورزشی", { status: "confirmed", checks })] });
  const holder = await open(t);
  assert.ok(holder.textContent.includes("رقیب‌ها: b.example در ۱۰ نتیجهٔ اول نیست · new.example هنوز بررسی نشده"));
});
