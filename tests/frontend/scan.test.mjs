// The site scan page: from the first look to the two files on the site.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, findAll, settle } from "./fake_dom.mjs";
import { scanPage } from "../../frontend/js/pages/scan.js";

const SITE = { site_id: "site-1", base_url: "https://shop.test/" };

function scan(extra = {}) {
  return {
    status: "completed", requested_at: new Date().toISOString(), completed_at: "2026-11-01T10:00:00Z", robots_text: null, sitemap_urls: [], sitemap_page_count: 0,
    pages: [{ url: "https://shop.test/", status_code: 200 }], pages_capped: false,
    issues: [
      { code: "no_sitemap", severity: "warning", url: null, detail: {} },
      { code: "missing_title", severity: "critical", url: "https://shop.test/a", detail: {} },
      { code: "missing_title", severity: "critical", url: "https://shop.test/b", detail: {} },
    ],
    ...extra,
  };
}

function change(kind, extra = {}) {
  return {
    kind, status: "proposed", public_url: `https://shop.test/${kind === "robots" ? "robots.txt" : "sitemap.xml"}`,
    proposed_text: "text", previous_text: null, changes: ["created"], note: null,
    ...extra,
  };
}

function targets(extra = {}) {
  return {
    site_id: "site-1", description: "", seeds: [], keywords: [], suggestion_status: "idle",
    pages: [{ url: "https://shop.test/", title: "خانه" }, { url: "https://shop.test/a", title: "کفش ورزشی" }],
    ...extra,
  };
}

let server;

beforeEach(() => {
  server = { scan: null, files: [], posts: [], postStatus: 202, onPost: () => {}, targets: targets() };
  globalThis.fetch = async (url, init) => {
    if (init.method === "POST") {
      server.posts.push(url.pathname);
      if (server.postStatus >= 400) return answer(server.postStatus, { detail: "no" });
      server.onPost(url.pathname);
      return answer(server.postStatus, {});
    }
    if (url.pathname.endsWith("/scans/latest")) return server.scan ? answer(200, server.scan) : answer(404, { detail: "none" });
    if (url.pathname.endsWith("/target-keywords")) return answer(200, server.targets);
    return answer(200, server.files);
  };
});

async function open(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const container = new FakeElement("main");
  await scanPage(container, { site: SITE, alive: () => true });
  return container;
}

test("a site never scanned offers a scan and says it needs only the address", async (t) => {
  const container = await open(t);
  assert.ok(container.textContent.includes("فقط آدرس سایت را لازم دارد"));
  server.onPost = () => { server.scan = scan({ status: "running" }); };

  button(container, "بررسی سایت").click();
  await settle();
  assert.deepEqual(server.posts, ["/v1/sites/site-1/scans"]);
  assert.ok(container.textContent.includes("در حال خواندن سایت"));

  server.scan = scan();
  t.mock.timers.tick(2000);
  await settle();
  assert.ok(container.textContent.includes("چیزهایی که پیدا شد"), "it looks again until the scan is done");
});

test("issues are grouped and named in plain words, never by their code", async (t) => {
  server.scan = scan();
  const container = await open(t);
  const text = container.textContent;
  assert.ok(text.includes("صفحه‌های بدون عنوان"));
  assert.ok(text.includes("سایت نقشه ندارد"));
  assert.ok(!text.includes("missing_title"));
  assert.equal(findAll(container, (element) => element.className.startsWith("issue issue-")).length, 2);
});

test("an issue we have no words for still reads as a sentence", async (t) => {
  server.scan = scan({ issues: [{ code: "brand_new_code", severity: "info", url: null, detail: {} }] });
  const container = await open(t);
  assert.ok(!container.textContent.includes("brand_new_code"));
});

test("the customer is warned before a rule keeping Google out is taken away", async (t) => {
  server.scan = scan();
  server.files = [change("robots", { changes: ["removed_block_all"] })];
  const container = await open(t);
  assert.ok(container.textContent.includes("اگر سایت عمداً بسته است"));
});

test("the file waits with where to put it, and a way to say it is there", async (t) => {
  server.scan = scan();
  server.files = [change("sitemap", { status: "waiting_for_customer", note: "http_404" })];
  const container = await open(t);
  assert.ok(container.textContent.includes("https://shop.test/sitemap.xml"));
  assert.ok(container.textContent.includes("هنوز فایلی در این آدرس دیده نمی‌شود"));
  const link = findAll(container, (element) => element.tagName === "a" && element.textContent === "دریافت فایل")[0];
  assert.equal(link.href, "/v1/sites/site-1/site-files/sitemap/content");

  server.onPost = () => { server.files = [change("sitemap", { status: "applied" })]; };
  button(container, "گذاشتم، بررسی کن").click();
  await settle();
  assert.deepEqual(server.posts, ["/v1/sites/site-1/site-files/sitemap/check"]);
  assert.ok(container.textContent.includes("روی سایت است"));
  assert.ok(container.textContent.includes("سرچ کنسول"), "the last step is named");
});

test("a refused step says so, and the page stays", async (t) => {
  server.scan = scan();
  server.files = [change("robots")];
  const container = await open(t);
  server.postStatus = 500;
  button(container, "رد کردن").click();
  await settle();
  const alert = findAll(container, (element) => element.attributes.role === "alert" && !element.classList.contains("hidden"));
  assert.equal(alert.length, 1);
  assert.ok(button(container, "رد کردن"));
});

test("a file put in place by hand keeps the old text for putting back by hand", async (t) => {
  server.scan = scan();
  server.files = [change("robots", { status: "applied", previous_text: "OLD TEXT" })];
  const container = await open(t);
  assert.ok(container.textContent.includes("OLD TEXT"));
  assert.ok(!container.textContent.includes("برگرداندن این تغییر"));
});

test("a scan that stopped early says it did not read everything", async (t) => {
  server.scan = scan({ pages_capped: true });
  const container = await open(t);
  assert.ok(container.textContent.includes("همهٔ صفحه‌ها را پوشش نمی‌دهد"));
});

test("a failed scan says so", async (t) => {
  server.scan = scan({ status: "failed" });
  const container = await open(t);
  assert.ok(container.textContent.includes("این بررسی کامل نشد"));
});

test("a scan lost to a restart gives the button back", async (t) => {
  server.scan = scan({ status: "running", requested_at: "2020-01-01T00:00:00Z" });
  const container = await open(t);
  const again = button(container, "بررسی دوباره");
  assert.equal(again.disabled, false);
  assert.ok(container.textContent.includes("این بررسی کامل نشد"));
});

test("a running scan keeps the button off", async (t) => {
  server.scan = scan({ status: "running", requested_at: new Date().toISOString() });
  const container = await open(t);
  assert.equal(button(container, "بررسی دوباره").disabled, true);
});

test("a proposal the site no longer needs is not shown", async (t) => {
  server.scan = scan();
  server.files = [change("robots", { status: "not_needed" })];
  const container = await open(t);
  assert.ok(!container.textContent.includes("فایل robots.txt"));
});

test("approving hands over the file, and nothing claims to be written to the site", async (t) => {
  server.scan = scan();
  server.files = [change("robots")];
  const container = await open(t);
  assert.ok(button(container, "تأیید و گرفتن فایل"));
  assert.ok(!container.textContent.includes("اعمال روی سایت"));
});

test("the file stays in reach whatever the scan is doing", async (t) => {
  server.files = [change("sitemap", { status: "waiting_for_customer" })];
  for (const status of ["failed", "running"]) {
    server.scan = scan({ status });
    const container = await open(t);
    assert.ok(button(container, "گذاشتم، بررسی کن"), status);
    t.mock.timers.reset();
  }
});

test("an old sitemap still in place is told apart from the new one", async (t) => {
  server.scan = scan();
  server.files = [change("sitemap", { status: "waiting_for_customer", note: "different_content" })];
  const container = await open(t);
  assert.ok(container.textContent.includes("متنش با این نسخه یکی نیست"));
});

test("the text that was there before is kept for putting back", async (t) => {
  server.scan = scan();
  server.files = [change("robots", { status: "waiting_for_customer", previous_text: "OLD TEXT" })];
  const container = await open(t);
  assert.ok(container.textContent.includes("OLD TEXT"));
});

test("an improved sitemap names the pages it takes out", async (t) => {
  server.scan = scan();
  server.files = [change("sitemap", { changes: ["improved"], dropped: ["https://shop.test/gone"] })];
  const container = await open(t);
  assert.ok(container.textContent.includes("۱ صفحه از نقشه بیرون می‌رود"));
  assert.ok(container.textContent.includes("https://shop.test/gone"));
});

test("the summary leads with the health score", async (t) => {
  server.scan = scan({ health_score: 82 });
  const container = await open(t);
  assert.ok(container.textContent.includes("امتیاز سلامت"));
  assert.ok(container.textContent.includes("۸۲ از ۱۰۰"));
});


test("the summary gives the usual response time, not the slowest", async (t) => {
  server.scan = scan({ pages: [600, 900, 4000].map((ms, n) => ({ url: `https://shop.test/${n}`, status_code: 200, speed: { fetch_ms: ms, html_kb: 10 } })) });
  const container = await open(t);
  assert.ok(container.textContent.includes("زمان پاسخ معمول"));
  assert.ok(container.textContent.includes("۰٫۹ ثانیه"));
});


test("an old scan without speed shows no response time", async (t) => {
  server.scan = scan();
  const container = await open(t);
  assert.ok(!container.textContent.includes("زمان پاسخ معمول"));
});
