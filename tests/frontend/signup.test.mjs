// Signing up with a mobile number, and the subscription page.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { FakeElement, answer, button, findAll, settle, type } from "./fake_dom.mjs";
import { loginPage } from "../../frontend/js/pages/login.js";
import { billingPage } from "../../frontend/js/pages/billing.js";

let server;

beforeEach(() => {
  server = { options: { mobile: true, signup: true, google: true }, calls: [], verify: () => answer(200, { csrf_token: "c" }), billing: null, checkout: () => answer(200, { url: "https://pay.example/StartPay/A1" }) };
  globalThis.window = { location: { hash: "#/billing", origin: "https://hoshyarseo.ir", assign: (url) => { server.assigned = url; } } };
  globalThis.fetch = async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : null;
    server.calls.push([url.pathname, body]);
    if (url.pathname === "/v1/auth/options") return answer(200, server.options);
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
  assert.deepEqual(server.calls.find(([path]) => path === "/v1/auth/mobile/code"), ["/v1/auth/mobile/code", { mobile: "۰۹۱۲۰۰۰۰۰۰۱" }]);
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
    plans: [
      { plan: "basic", name: "پایه", sites: 1, price: 890000, changes_a_month: 20, targets: 10, rescan_days: 30, best_writer: false },
      { plan: "gold", name: "طلایی", sites: 1, price: 3500000, changes_a_month: 100, targets: 50, rescan_days: 7, best_writer: true },
    ],
    changes: { used: 20, limit: 20, frees_at: "2026-12-01T10:00:00Z" },
    payments: [], ...extra,
  };
}

test("the subscription page shows the time left and sends the customer to pay", async () => {
  server.billing = billing();
  const container = new FakeElement("main");
  await billingPage(container, { param: null, alive: () => true });
  assert.ok(container.textContent.includes("دورهٔ آزمایشی رایگان: ۷ روز مانده است."));
  assert.ok(container.textContent.includes("۸۹۰٬۰۰۰ تومان") || container.textContent.includes("۸۹۰,۰۰۰ تومان"));
  // What each plan gives, as the server says, and how much of this month is used.
  assert.ok(container.textContent.includes("عنوان و توضیحات با قوی‌ترین مدل هوش مصنوعی"));
  assert.ok(container.textContent.includes("بررسی خودکار سایت هر هفته"));
  assert.ok(container.textContent.includes("بررسی خودکار سایت هر ماه"));
  assert.ok(container.textContent.includes("تغییرهای سئوی ۳۰ روز اخیر: ۲۰ از ۲۰"));
  assert.ok(container.textContent.includes("آزاد می‌شود"));

  button(container, "خرید یک ماه").click();
  await settle();
  assert.deepEqual(server.calls.at(-1), ["/v1/billing/checkout", { plan: "basic" }]);
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


test("a server without sign-in by mobile shows the email form alone, as before", async () => {
  server.options = { mobile: false, signup: false, google: false };
  const page = loginPage({ onSignedIn: async () => {} });
  await settle();
  const mobileArea = findAll(page, (element) => element.tagName === "div" && findAll(element, (child) => child.id === "mobile").length && element.className === "hidden");
  assert.ok(mobileArea.length, "the mobile form stays hidden");
  assert.ok(page.textContent.includes("ورود به حساب"));
  const [google] = findAll(page, (element) => element.tagName === "a" && element.href === "/v1/auth/google/start");
  assert.ok(google.className.includes("hidden"));
});

test("with sign-up open the page says it signs up too", async () => {
  const page = loginPage({ onSignedIn: async () => {} });
  await settle();
  assert.ok(page.textContent.includes("ورود یا ثبت‌نام"));
});


test("when the server cannot be asked, email and Google stay as they were", async () => {
  server.options = null;
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, init) => (url.pathname === "/v1/auth/options" ? answer(500, { detail: "down" }) : saved(url, init));
  const page = loginPage({ onSignedIn: async () => {} });
  await settle();
  const [google] = findAll(page, (element) => element.tagName === "a" && element.href === "/v1/auth/google/start");
  assert.ok(!google.className.includes("hidden"));
  const [email] = findAll(page, (element) => element.tagName === "details");
  assert.notEqual(email.open, false, "never closed: the email form is the way in");
  assert.ok(input(page, "email"));
});
