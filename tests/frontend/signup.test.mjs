// Signing up with a mobile number, and the subscription page.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, findAll, settle, type } from "./fake_dom.mjs";
import { loginPage } from "../../frontend/js/pages/login.js";
import { billingPage } from "../../frontend/js/pages/billing.js";

let server;

beforeEach(() => {
  server = { calls: [], verify: () => answer(200, { csrf_token: "c" }), billing: null, checkout: () => answer(200, { url: "https://pay.example/StartPay/A1" }) };
  globalThis.window = { location: { hash: "#/billing", origin: "https://hoshyarseo.ir", assign: (url) => { server.assigned = url; } } };
  globalThis.fetch = async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : null;
    server.calls.push([url.pathname, body]);
    if (url.pathname === "/v1/auth/mobile/code") return answer(202, { sent: true, wait_seconds: 60 });
    if (url.pathname === "/v1/auth/mobile/verify") return server.verify(body);
    if (url.pathname === "/v1/billing") return answer(200, server.billing);
    if (url.pathname === "/v1/billing/checkout") return server.checkout(body);
    return answer(404, { detail: "none" });
  };
});

function input(root, id) {
  return findAll(root, (element) => element.id === id)[0];
}

async function submit(root) {
  const [form] = findAll(root, (element) => element.tagName === "form");
  form.dispatch("submit");
  await settle();
}

test("a new number is asked for the business and the terms, then signed in", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let signedIn = 0;
  const page = loginPage({ onSignedIn: async () => { signedIn += 1; } });
  type(input(page, "mobile"), "۰۹۱۲۰۰۰۰۰۰۱");
  await submit(page);
  assert.deepEqual(server.calls[0], ["/v1/auth/mobile/code", { mobile: "۰۹۱۲۰۰۰۰۰۰۱" }]);
  assert.ok(page.textContent.includes("ارسال دوباره تا ۶۰ ثانیهٔ دیگر"));

  server.verify = (body) => answer(200, body.business_name ? { csrf_token: "c" } : { status: "needs_signup" });
  type(input(page, "code"), "123456");
  await submit(page);
  assert.equal(signedIn, 0);
  assert.ok(page.textContent.includes("این شماره هنوز حساب ندارد"));

  await submit(page);
  assert.ok(page.textContent.includes("نام کسب‌وکار را بنویسید و قوانین را بپذیرید"), "nothing is sent without both");

  type(input(page, "business"), "فروشگاه آریا");
  input(page, "terms").checked = true;
  await submit(page);
  assert.deepEqual(server.calls.at(-1), ["/v1/auth/mobile/verify", { mobile: "۰۹۱۲۰۰۰۰۰۰۱", code: "123456", business_name: "فروشگاه آریا", accept_terms: true }]);
  assert.equal(signedIn, 1);
});

test("a known number goes straight in with its code", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let signedIn = 0;
  const page = loginPage({ onSignedIn: async () => { signedIn += 1; } });
  type(input(page, "mobile"), "09120000001");
  await submit(page);
  type(input(page, "code"), "123456");
  await submit(page);
  assert.equal(signedIn, 1);
});

test("a wrong code says so in Persian", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  server.verify = () => answer(401, { detail: "The code is not correct or has expired." });
  const page = loginPage({ onSignedIn: async () => {} });
  type(input(page, "mobile"), "09120000001");
  await submit(page);
  type(input(page, "code"), "000000");
  await submit(page);
  assert.ok(page.textContent.includes("کد درست نیست یا وقتش تمام شده است"));
});

function billing(extra = {}) {
  return {
    plan: "trial", plan_name: "آزمایشی", active: true, days_left: 7, sites_used: 0, sites_limit: 1, can_pay: true,
    plans: [{ plan: "starter", name: "پایه", sites: 1, price: 490000 }, { plan: "growth", name: "رشد", sites: 3, price: 1290000 }],
    payments: [], ...extra,
  };
}

test("the subscription page shows the time left and sends the customer to pay", async () => {
  server.billing = billing();
  const container = new FakeElement("main");
  await billingPage(container, { param: null, alive: () => true });
  assert.ok(container.textContent.includes("دورهٔ آزمایشی رایگان: ۷ روز مانده است."));
  assert.ok(container.textContent.includes("۴۹۰٬۰۰۰ تومان") || container.textContent.includes("۴۹۰,۰۰۰ تومان"));

  button(container, "خرید یک ماه").click();
  await settle();
  assert.deepEqual(server.calls.at(-1), ["/v1/billing/checkout", { plan: "starter" }]);
  assert.equal(server.assigned, "https://pay.example/StartPay/A1");
});

test("an ended plan and a payment's outcome are said plainly", async () => {
  server.billing = billing({ active: false, days_left: 0 });
  const container = new FakeElement("main");
  await billingPage(container, { param: "?payment=failed", alive: () => true });
  assert.ok(container.textContent.includes("اشتراک شما تمام شده است"));
  assert.ok(container.textContent.includes("پرداخت انجام نشد"));
});

test("without online payment the buttons wait and the page says why", async () => {
  server.billing = billing({ can_pay: false });
  const container = new FakeElement("main");
  await billingPage(container, { param: null, alive: () => true });
  assert.ok(container.textContent.includes("پرداخت آنلاین به‌زودی فعال می‌شود"));
  assert.equal(button(container, "خرید یک ماه").disabled, true);
});

test("a number typed wrong can be changed before the code", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const page = loginPage({ onSignedIn: async () => {} });
  type(input(page, "mobile"), "09120000009");
  await submit(page);
  assert.equal(input(page, "mobile").disabled, true);
  button(page, "تغییر شماره").click();
  assert.equal(input(page, "mobile").disabled, false);
  type(input(page, "mobile"), "09120000001");
  await submit(page);
  assert.deepEqual(server.calls.at(-1), ["/v1/auth/mobile/code", { mobile: "09120000001" }]);
});

test("an account without a plan has nothing to buy", async () => {
  server.billing = billing({ plan: null, plan_name: null, sites_limit: null, can_pay: true });
  const container = new FakeElement("main");
  await billingPage(container, { param: null, alive: () => true });
  assert.ok(container.textContent.includes("حساب شما محدودیت پلن ندارد."));
  assert.equal(findAll(container, (element) => element.tagName === "button").length, 0);
});
