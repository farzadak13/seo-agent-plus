// The owner's split sign-in page. Google first: it is also how Search Console gets connected.

import { api, messageFor } from "../api.js";
import { h, s } from "../dom.js";

function googleIcon() {
  const paths = [
    ["#4285F4", "M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"],
    ["#34A853", "M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"],
    ["#FBBC05", "M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"],
    ["#EA4335", "M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"],
  ];
  return s("svg", { class: "google-icon", viewBox: "0 0 24 24", "aria-hidden": "true" },
    paths.map(([fill, d]) => s("path", { fill, d })));
}

export function loginPage({ onSignedIn }) {
  const problem = h("div", { class: "notice notice-error hidden", role: "alert" });
  const email = h("input", { class: "input ltr", id: "email", type: "email", required: true, autocomplete: "username", placeholder: "name@company.com" });
  const password = h("input", { class: "input ltr", id: "password", type: "password", required: true, autocomplete: "current-password", placeholder: "••••••••" });
  const submit = h("button", { class: "btn btn-primary btn-submit", type: "submit", text: "ورود به پلتفرم" });

  const form = h("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      problem.classList.add("hidden");
      submit.disabled = true;
      submit.textContent = "در حال ورود…";
      try {
        await api("/v1/auth/login", {
          method: "POST", quiet: true,
          body: { email: email.value.trim(), password: password.value },
        });
        password.value = "";
        await onSignedIn();
      } catch (error) {
        problem.textContent = messageFor(error);
        problem.classList.remove("hidden");
        submit.disabled = false;
        submit.textContent = "ورود به پلتفرم";
      }
    },
  },
    h("div", { class: "field" }, h("label", { for: "email", text: "آدرس ایمیل" }), email),
    h("div", { class: "field" }, h("label", { for: "password", text: "رمز عبور" }), password),
    submit,
  );

  return h("div", { class: "auth" },
    h("section", { class: "auth-section" },
      h("div", { class: "auth-wrapper" },
        h("div", { class: "brand-mobile" }, h("span", { class: "gold-text", text: "✦" }), " هوشیار سئو"),
        h("div", { class: "auth-header" },
          h("h1", { text: "ورود به حساب" }),
          h("p", { text: "برای مدیریت پروژه‌های سئو وارد شوید." }),
        ),
        problem,
        h("a", { class: "btn btn-google", href: "/v1/auth/google/start" }, googleIcon(), "ورود امن با حساب گوگل"),
        h("div", { class: "divider", text: "یا ورود با ایمیل" }),
        form,
      ),
    ),
    h("section", { class: "showcase", "aria-hidden": "true" },
      h("div", { class: "glow glow-1" }),
      h("div", { class: "glow glow-2" }),
      h("div", { class: "brand-logo" }, h("span", { class: "gold-text", text: "✦" }), " هوشیار سئو"),
      h("div", { class: "floating-card" },
        h("div", { class: "fc-title", text: "تصمیم با شماست" }),
        h("div", { class: "fc-value", text: "هیچ تغییری بدون تأیید شما اعمال نمی‌شود" }),
        h("div", { class: "fc-note", text: "و هر تغییر را می‌توانید برگردانید." }),
      ),
      h("div", { class: "showcase-content" },
        h("h2", {}, "دستیار هوشمند شما برای ", h("span", { class: "gold-text", text: "تسخیر صفحه اول" })),
        h("p", { text: "داده‌های گوگل سرچ کنسول را به پیشنهادهای عملی تبدیل کنید: تحلیل دقیق، بررسی رقبا و سنجش نتیجهٔ هر تغییر با دادهٔ واقعی." }),
      ),
    ),
  );
}
