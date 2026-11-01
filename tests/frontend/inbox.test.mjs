// The approval inbox: above all, the title that gets approved is the one chosen.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, choose, findAll, settle, type } from "./fake_dom.mjs";
import { inboxPage } from "../../frontend/js/pages/inbox.js";

const WORDPRESS = { site_id: "site-1", site_adapter_type: "hoshyarseo" };
const MANUAL = { site_id: "site-1", site_adapter_type: "manual" };
const UPDATING = "در حال اعمال و بررسی روی سایت…";

function proposal(id, extra = {}) {
  return {
    action_id: id, status: "awaiting_approval", normalized_url: `https://example.test/${id}`, normalized_query: `query ${id}`,
    current_title: "current", proposed_title: `proposed ${id}`, alternative_titles: [`other ${id}`],
    history: [], applied: null, rolled_back: false, error: null, updated_at: "2026-10-01T00:00:00Z",
    ...extra,
  };
}

function appliedChange(extra = {}) {
  return proposal("a", {
    status: "executed",
    applied: { previous_value: "old title", new_value: "new title", applied_at: "2026-10-01T00:00:00Z" },
    ...extra,
  });
}

let server;

beforeEach(() => {
  server = {
    actions: [], posts: [], listFails: false, postFails: false,
    onPost: () => {},
  };
  globalThis.fetch = async (url, init) => {
    if (init.method === "POST") {
      if (server.postFails) return answer(500, { detail: "no" });
      const body = JSON.parse(init.body);
      server.posts.push({ path: decodeURIComponent(url.pathname), body });
      server.onPost(decodeURIComponent(url.pathname));
      return answer(202, {});
    }
    if (server.listFails) return answer(500, { detail: "no" });
    return answer(200, { actions: server.actions, next_cursor: null });
  };
});

async function open(site = WORDPRESS) {
  const container = new FakeElement("main");
  await inboxPage(container, { site, param: null, alive: () => true, refreshCount: () => {} });
  return { container, card: (id) => findAll(container, (element) => element.id === `action-${id}`)[0] };
}

const checked = (card) => findAll(card, (element) => element.type === "radio" && element.checked).map((radio) => radio.value);
const titleBox = (card) => findAll(card, (element) => element.tagName === "input" && element.type === "text")[0];
const notices = (card) => findAll(card, (element) => element.attributes.role === "alert" && !element.classList.contains("hidden"));

// Card a moves on while b waits: this is what used to turn b back to its first title.
async function approveTheOtherCard(page, t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  server.onPost = () => { server.actions = [proposal("a", { status: "approved" }), server.actions[1]]; };
  const before = page.card("b");
  button(page.card("a"), "تأیید و اعمال روی سایت").click();
  await settle();
  assert.notEqual(page.card("b"), before, "the list has to have been redrawn for this to prove anything");
  server.posts = [];
}

test("a chosen title survives another card moving on, and is the one approved", async (t) => {
  server.actions = [proposal("a"), proposal("b")];
  const page = await open();
  choose(page.card("b"), "other b");

  await approveTheOtherCard(page, t);

  assert.deepEqual(checked(page.card("b")), ["other b"]);
  button(page.card("b"), "تأیید و اعمال روی سایت").click();
  await settle();
  assert.deepEqual(server.posts, [{ path: "/v1/actions/b/approve", body: { title: "other b" } }]);
});

test("a typed title survives another card moving on, and is approved trimmed", async (t) => {
  server.actions = [proposal("a"), proposal("b")];
  const page = await open();
  type(titleBox(page.card("b")), "  my own title ");

  await approveTheOtherCard(page, t);

  assert.deepEqual(checked(page.card("b")), ["__custom"]);
  assert.equal(titleBox(page.card("b")).value, "  my own title ");
  button(page.card("b"), "تأیید و اعمال روی سایت").click();
  await settle();
  assert.deepEqual(server.posts, [{ path: "/v1/actions/b/approve", body: { title: "my own title" } }]);
});

test("a choice survives going to another tab and back", async () => {
  server.actions = [proposal("a")];
  const page = await open();
  choose(page.card("a"), "other a");

  findAll(page.container, (element) => element.attributes.role === "tab")[1].click();
  assert.equal(page.card("a"), undefined);
  findAll(page.container, (element) => element.attributes.role === "tab")[0].click();

  assert.deepEqual(checked(page.card("a")), ["other a"]);
});

test("one card's choice is not another's", async () => {
  server.actions = [proposal("a"), proposal("b")];
  const page = await open();
  choose(page.card("a"), "other a");
  assert.deepEqual(checked(page.card("b")), ["proposed b"]);
});

test("the proposed title goes without a title, as nothing was changed", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  server.actions = [proposal("a")];
  const page = await open();
  button(page.card("a"), "تأیید و اعمال روی سایت").click();
  await settle();
  assert.deepEqual(server.posts, [{ path: "/v1/actions/a/approve", body: {} }]);
});

test("an empty title of one's own approves nothing", async () => {
  server.actions = [proposal("a")];
  const page = await open();
  type(titleBox(page.card("a")), "   ");
  button(page.card("a"), "تأیید و اعمال روی سایت").click();
  await settle();
  assert.deepEqual(server.posts, []);
  assert.ok(titleBox(page.card("a")).focused);
});

test("a refused approval says so and can be tried again, with the choice intact", async () => {
  server.actions = [proposal("a")];
  const page = await open();
  choose(page.card("a"), "other a");
  server.postFails = true;
  const approve = button(page.card("a"), "تأیید و اعمال روی سایت");
  approve.click();
  await settle();
  assert.equal(notices(page.card("a")).length, 1);
  assert.equal(approve.disabled, false);
  assert.deepEqual(checked(page.card("a")), ["other a"]);
});

test("done but the list could not be fetched: it says so, stays off, then catches up", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  server.actions = [proposal("a")];
  const page = await open();
  server.onPost = () => { server.actions = [proposal("a", { status: "rejected" })]; };
  server.listFails = true;
  const reject = button(page.card("a"), "رد کردن");

  reject.click();
  await settle();
  assert.equal(reject.disabled, true, "pressing it again could only be refused");
  assert.match(notices(page.card("a"))[0].textContent, /ثبت شد/);

  server.listFails = false;
  t.mock.timers.tick(2000);
  await settle();
  assert.equal(page.card("a"), undefined, "rejected, it has left the waiting tab");
});

test("a change with an error does not also claim to be under way", async () => {
  server.actions = [appliedChange({ status: "executing", applied: null, error: "boom" })];
  const { container } = await open();
  findAll(container, (element) => element.attributes.role === "tab")[1].click();
  assert.ok(!container.textContent.includes(UPDATING));
  assert.ok(container.textContent.includes("این مرحله با خطا روبه‌رو شد"));
});

test("a change on its way says so", async () => {
  server.actions = [appliedChange({ status: "executing", applied: null })];
  const { container } = await open();
  assert.ok(container.textContent.includes(UPDATING));
});

const WAITING_TO_RESTORE = [{ reason: "waiting_for_customer_to_restore", at: "2026-10-02T00:00:00Z", detail: { restore_title: "old title" } }];

test("undoing by hand on a manual site asks for the old title back", async () => {
  server.actions = [appliedChange({ history: WAITING_TO_RESTORE })];
  const { container } = await open(MANUAL);
  assert.ok(button(container, "برگرداندم، بررسی کن"));
});

test("if the connection changed since, the undo can be asked for again", async () => {
  server.actions = [appliedChange({ history: WAITING_TO_RESTORE })];
  const { container } = await open(WORDPRESS);
  assert.ok(button(container, "برگرداندن این تغییر"));
  assert.ok(!container.textContent.includes("در حال برگرداندن تغییر…"));
});

test("an undo with a job on it says it is running", async () => {
  server.actions = [appliedChange({ history: [{ reason: "rollback_requested", at: "2026-10-02T00:00:00Z", detail: {} }] })];
  const { container } = await open(WORDPRESS);
  assert.ok(container.textContent.includes("در حال برگرداندن تغییر…"));
});
