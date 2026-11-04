// اشتراک: the plan, the time left, and paying for a month by Zarinpal.

import { api, messageFor } from "../api.js";
import { h, fmt } from "../dom.js";

const RESULT = {
  paid: { tone: "good", text: "پرداخت انجام شد و اشتراکتان یک ماه تمدید شد." },
  failed: { tone: "error", text: "پرداخت انجام نشد. اگر مبلغی از حسابتان کم شده، بانک ظرف ۷۲ ساعت آن را برمی‌گرداند." },
  pending: { tone: "warn", text: "نتیجهٔ پرداخت هنوز از درگاه نیامده است. چند دقیقهٔ دیگر این صفحه را تازه کنید." },
};

const toman = (amount) => `${fmt.int(amount)} تومان`;

function buttonText(state, plan) {
  if (state.plan === plan.plan && state.active) return "تمدید یک ماه";
  if (state.active && state.plan && state.plan !== "trial") return "تغییر به این پلن";
  return "خرید یک ماه";
}

function statusText(state) {
  if (!state.plan) return "حساب شما محدودیت پلن ندارد.";
  if (!state.active) return "اشتراک شما تمام شده است. اطلاعات و تغییرهای قبلی سر جایشان هستند؛ برای انجام کارهای جدید، اشتراک را تمدید کنید.";
  const days = state.days_left === 0 ? "کمتر از یک روز" : `${fmt.int(state.days_left)} روز`;
  return state.plan === "trial"
    ? `دورهٔ آزمایشی رایگان: ${days} مانده است.`
    : `پلن ${state.plan_name}: ${days} از اشتراک مانده است.`;
}

export async function billingPage(container, { param, alive }) {
  const state = await api("/v1/billing");
  if (!alive()) return;
  const outcome = new URLSearchParams((param || "").replace(/^\?/, "")).get("payment")
    || new URLSearchParams(window.location.hash.split("?")[1] || "").get("payment");
  const problem = h("div", { class: "notice notice-error hidden", role: "alert" });

  const pay = (plan) => {
    const button = h("button", {
      class: "btn btn-primary", type: "button", text: buttonText(state, plan),
      disabled: !state.can_pay,
      onclick: async () => {
        button.disabled = true;
        problem.classList.add("hidden");
        try {
          const { url } = await api("/v1/billing/checkout", { method: "POST", body: { plan: plan.plan } });
          window.location.assign(url);
        } catch (error) {
          problem.textContent = messageFor(error);
          problem.classList.remove("hidden");
          button.disabled = false;
        }
      },
    });
    return button;
  };

  container.append(
    h("div", { class: "page-header" },
      h("div", {}, h("h1", { class: "page-title", text: "اشتراک" }), h("p", { class: "page-sub", text: statusText(state) })),
    ),
    outcome && RESULT[outcome] ? h("div", { class: `notice notice-${RESULT[outcome].tone}`, role: "status", text: RESULT[outcome].text }) : "",
    problem,
    state.can_pay || !state.plan ? "" : h("div", { class: "notice notice-warn", text: "پرداخت آنلاین به‌زودی فعال می‌شود. تا آن موقع برای خرید با پشتیبانی تماس بگیرید." }),
    state.active && state.plan && state.plan !== "trial"
      ? h("p", { class: "small muted", text: "اگر پلن دیگری بخرید، روزهای باقی‌ماندهٔ پلن فعلی به نسبت قیمت به پلن تازه اضافه می‌شود." })
      : "",
    h("section", { class: "plans" },
      state.plans.map((plan) => h("article", { class: `plan-card${state.plan === plan.plan ? " plan-current" : ""}` },
        h("div", { class: "plan-name", text: plan.name }),
        h("div", { class: "plan-price" }, toman(plan.price), h("span", { class: "small muted", text: " / ماه" })),
        h("ul", { class: "plan-features" },
          h("li", { text: plan.sites === 1 ? "یک سایت" : `تا ${fmt.int(plan.sites)} سایت` }),
          h("li", { text: "بررسی سایت، robots.txt و نقشهٔ سایت" }),
          h("li", { text: "پیشنهاد و اعمال عنوان با تأیید شما" }),
          h("li", { text: "کلمه‌های هدف و رتبهٔ هفتگی" }),
        ),
        state.plan ? pay(plan) : "",
      )),
    ),
    state.sites_limit
      ? h("p", { class: "small muted", text: `سایت‌های این حساب: ${fmt.int(state.sites_used)} از ${fmt.int(state.sites_limit)}` })
      : "",
    state.payments.length
      ? h("section", { class: "card-panel" },
          h("div", { class: "panel-title", text: "پرداخت‌ها" }),
          h("table", { class: "table" },
            h("thead", {}, h("tr", {}, ["تاریخ", "پلن", "مبلغ", "وضعیت", "کد پیگیری"].map((text) => h("th", { text })))),
            h("tbody", {}, state.payments.map((payment) => h("tr", {},
              h("td", { text: fmt.moment(payment.created_at) }),
              h("td", { text: state.plans.find((plan) => plan.plan === payment.plan)?.name || payment.plan }),
              h("td", { text: toman(payment.amount) }),
              h("td", { text: { paid: "موفق", failed: "ناموفق", pending: "در انتظار" }[payment.status] || payment.status }),
              h("td", { class: "ltr", text: payment.ref_id || "—" }),
            ))),
          ),
        )
      : "",
  );
}
