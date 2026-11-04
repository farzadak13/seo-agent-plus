// تنظیمات پروژه: add a site and take it through every connection it needs.

import { api, messageFor } from "../api.js";
import { h, clear, fmt } from "../dom.js";

function step(number, done, title, ...body) {
  return h("div", { class: `step${done ? " done" : ""}` },
    h("div", { class: "step-mark", "aria-hidden": "true", text: done ? "✓" : fmt.int(number) }),
    h("div", { class: "step-body" },
      h("div", { class: "step-title" }, title, done ? h("span", { class: "sr-only", text: " (انجام شده)" }) : null),
      body,
    ),
  );
}

function problemBox() {
  const box = h("div", { class: "notice notice-error hidden", role: "alert" });
  box.show = (error) => {
    box.textContent = typeof error === "string" ? error : messageFor(error);
    box.classList.remove("hidden");
  };
  box.hide = () => box.classList.add("hidden");
  return box;
}

function addSiteForm({ refreshSites, rerender, first }) {
  const problem = problemBox();
  const name = h("input", { class: "input", id: "site-name", required: true, maxLength: 200, placeholder: "مثلاً فروشگاه من" });
  const url = h("input", { class: "input ltr", id: "site-url", required: true, type: "url", placeholder: "https://example.com" });
  const submit = h("button", { class: "btn btn-primary", type: "submit", text: "افزودن سایت" });
  return h("section", { class: "card-panel" },
    h("div", { class: "panel-title", text: first ? "اولین سایتتان را اضافه کنید" : "افزودن سایت تازه" }),
    problem,
    h("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        problem.hide();
        submit.disabled = true;
        try {
          const site = await api("/v1/sites", { method: "POST", body: { name: name.value.trim(), base_url: url.value.trim() } });
          await refreshSites(site.site_id);
          // A new site starts with a look at it; connecting comes after.
          await api(`/v1/sites/${encodeURIComponent(site.site_id)}/scans`, { method: "POST", body: {} }).catch(() => null);
          window.location.hash = "#/scan";
        } catch (error) {
          problem.show(error);
          submit.disabled = false;
        }
      },
    },
      h("div", { class: "field" }, h("label", { for: "site-name", text: "نام سایت" }), name),
      h("div", { class: "field" }, h("label", { for: "site-url", text: "آدرس سایت" }), url),
      submit,
    ),
  );
}

async function googleStep(context) {
  const box = h("div", {});
  const problem = problemBox();
  let connection = { connected: false };
  let available = true;
  try {
    connection = await api("/v1/google/connection");
  } catch (error) {
    if (error.status === 503) available = false;
    else problem.show(error);
  }
  if (!available) {
    box.append(h("p", { class: "muted small", text: "ورود با گوگل روی این سرور فعال نیست." }));
    return { node: box, connected: false };
  }
  box.append(problem);
  if (connection.connected) {
    const disconnect = h("button", {
      class: "btn btn-danger", type: "button", text: "قطع اتصال گوگل",
      onclick: () => {
        const yes = h("button", {
          class: "btn btn-danger", type: "button", text: "بله، قطع کن",
          onclick: async () => {
            yes.disabled = true;
            try {
              await api("/v1/google/connection", { method: "DELETE" });
              await context.refreshSites();
              context.rerender();
            } catch (error) {
              problem.show(error);
              yes.disabled = false;
            }
          },
        });
        const no = h("button", { class: "btn", type: "button", text: "نه", onclick: () => holder.replaceChildren(disconnect) });
        holder.replaceChildren(h("span", { class: "small", text: "دسترسی به سرچ کنسول همهٔ سایت‌های شما قطع می‌شود." }), yes, no);
      },
    });
    const holder = h("div", { class: "step-actions" }, disconnect);
    box.append(
      h("p", { class: "small muted" }, "وصل با حساب ", h("span", { class: "ltr", text: connection.email || "" }), ". دسترسی فقط خواندنی است."),
      holder,
    );
  } else {
    const connect = h("button", {
      class: "btn btn-primary", type: "button", text: "وصل کردن حساب گوگل",
      onclick: async () => {
        connect.disabled = true;
        try {
          const { connect_url: url } = await api("/v1/google/connect", { method: "POST", body: {} });
          window.location.assign(url);
        } catch (error) {
          problem.show(error);
          connect.disabled = false;
        }
      },
    });
    box.append(
      h("p", { class: "small muted", text: "فقط اجازهٔ خواندن دادهٔ سرچ کنسول گرفته می‌شود. چیزی در حساب گوگل شما تغییر نمی‌کند." }),
      h("div", { class: "step-actions" }, connect),
    );
  }
  return { node: box, connected: Boolean(connection.connected) };
}

function propertyStep(site, googleConnected, context) {
  const box = h("div", {});
  if (site.gsc_configured) {
    box.append(h("p", { class: "small muted ltr", text: site.gsc_property_url }));
  }
  if (!googleConnected) {
    if (!site.gsc_configured) box.append(h("p", { class: "small muted", text: "اول حساب گوگل را وصل کنید." }));
    return box;
  }
  const problem = problemBox();
  const area = h("div", {});
  const load = h("button", {
    class: "btn", type: "button", text: site.gsc_configured ? "تغییر property" : "انتخاب property",
    onclick: async () => {
      load.disabled = true;
      problem.hide();
      try {
        const { properties } = await api(`/v1/sites/${encodeURIComponent(site.site_id)}/connections/gsc/available`, {
          method: "POST", body: { use_google_account: true },
        });
        const readable = properties.filter((item) => item.readable);
        clear(area);
        if (readable.length === 0) {
          area.append(h("p", { class: "small muted", text: "این حساب گوگل به هیچ propertyای در سرچ کنسول دسترسی ندارد." }));
          load.disabled = false;
          return;
        }
        // The property of this site's own domain first: usually the only right one.
        const host = new URL(site.base_url).host.replace(/^www\./, "");
        const covers = (item) => item.site_url.replace(/^sc-domain:|^https?:\/\/(www\.)?|\/$/g, "") === host;
        const ordered = [...readable].sort((a, b) => Number(covers(b)) - Number(covers(a)));
        const select = h("select", { class: "select ltr", "aria-label": "property سرچ کنسول" },
          ordered.map((item) => h("option", { value: item.site_url, text: item.display_name || item.site_url })));
        load.classList.add("hidden");
        const save = h("button", {
          class: "btn btn-primary", type: "button", text: "ذخیره",
          onclick: async () => {
            save.disabled = true;
            problem.hide();
            try {
              await api(`/v1/sites/${encodeURIComponent(site.site_id)}/connections/gsc`, {
                method: "PUT", body: { property_url: select.value, use_google_account: true },
              });
              await context.refreshSites();
              context.rerender();
            } catch (error) {
              problem.show(error);
              save.disabled = false;
            }
          },
        });
        area.append(select, h("div", { class: "step-actions" }, save));
      } catch (error) {
        problem.show(error);
        load.disabled = false;
      }
    },
  });
  box.append(problem, area, h("div", { class: "step-actions" }, load));
  return box;
}

function siteConnectionStep(site, context) {
  const problem = problemBox();
  const result = h("div", { class: "notice hidden", role: "status" });
  const box = h("div", {});
  const siteUrl = `/v1/sites/${encodeURIComponent(site.site_id)}/connections/site-adapter`;

  if (site.site_adapter_type) {
    box.append(h("p", { class: "small muted", text: site.site_adapter_type === "manual"
      ? "اعمال دستی: عنوان تأییدشده را خودتان در سایت می‌گذارید و هوشیار سئو صفحه را بررسی می‌کند."
      : "وردپرس با افزونهٔ HoshyarSEO Connector: عنوان تأییدشده مستقیم روی سایت اعمال می‌شود." }));
    // A manual connection holds nothing to test: the page is read when it is checked.
    const check = site.site_adapter_type === "manual" ? null : h("button", {
      class: "btn", type: "button", text: "آزمایش اتصال",
      onclick: async () => {
        check.disabled = true;
        result.classList.add("hidden");
        try {
          const answer = await api(`${siteUrl}/check`, { method: "POST", body: {} });
          result.className = `notice ${answer.ok ? "notice-good" : "notice-error"}`;
          result.textContent = answer.ok ? "اتصال سالم است." : "اتصال برقرار نشد. افزونه، نام کاربری و رمز برنامه را بررسی کنید.";
        } catch (error) {
          result.className = "notice notice-error";
          result.textContent = messageFor(error);
        }
        check.disabled = false;
      },
    });
    const change = h("button", { class: "btn", type: "button", text: "تغییر نوع اتصال", onclick: () => { form.classList.remove("hidden"); change.classList.add("hidden"); } });
    box.append(result, h("div", { class: "step-actions" }, check, change));
  }

  const group = `adapter-${site.site_id}`;
  const username = h("input", { class: "input ltr", id: "wp-user", autocomplete: "off" });
  const password = h("input", { class: "input ltr", id: "wp-pass", type: "password", autocomplete: "new-password" });
  const wordpressFields = h("div", { class: "hidden" },
    h("p", { class: "small muted" },
      "۱) ",
      h("a", { href: "downloads/hoshyarseo-connector.zip", text: "افزونهٔ HoshyarSEO Connector را دانلود کنید" }),
      " و در وردپرس نصب و فعال کنید. ۲) در وردپرس، پروفایل کاربر، یک «رمز برنامه» (Application Password) بسازید. ۳) این‌جا وارد کنید:",
    ),
    h("div", { class: "field" }, h("label", { for: "wp-user", text: "نام کاربری وردپرس" }), username),
    h("div", { class: "field" }, h("label", { for: "wp-pass", text: "رمز برنامه" }), password,
      h("div", { class: "hint", text: "رمزنگاری‌شده ذخیره می‌شود و هیچ‌وقت نمایش داده نمی‌شود." })),
  );
  const choose = (value) => wordpressFields.classList.toggle("hidden", value !== "hoshyarseo");
  const save = h("button", {
    class: "btn btn-primary", type: "button", text: "ذخیرهٔ اتصال",
    onclick: async () => {
      problem.hide();
      const type = form.querySelector(`input[name="${group}"]:checked`)?.value;
      if (!type) { problem.show("یکی از دو روش را انتخاب کنید."); return; }
      const body = { adapter_type: type, config: {}, secrets: {} };
      if (type === "hoshyarseo") {
        if (!username.value.trim() || !password.value.trim()) { problem.show("نام کاربری و رمز برنامه را وارد کنید."); return; }
        body.secrets = { username: username.value.trim(), application_password: password.value };
      }
      save.disabled = true;
      try {
        await api(siteUrl, { method: "PUT", body });
        password.value = "";
        await context.refreshSites();
        context.rerender();
      } catch (error) {
        problem.show(error);
        save.disabled = false;
      }
    },
  });
  const form = h("div", { class: site.site_adapter_type ? "hidden" : "" },
    problem,
    h("div", { class: "choice", role: "radiogroup", "aria-label": "روش اعمال تغییرها" },
      h("label", { class: "option" },
        h("input", { type: "radio", name: group, value: "hoshyarseo", onchange: () => choose("hoshyarseo") }),
        h("span", {}, h("strong", { text: "وردپرس" }), " — عنوان تأییدشده خودکار روی سایت اعمال می‌شود."),
      ),
      h("label", { class: "option" },
        h("input", { type: "radio", name: group, value: "manual", onchange: () => choose("manual") }),
        h("span", {}, h("strong", { text: "هر سایت دیگر (اعمال دستی)" }), " — عنوان را خودتان در سایت می‌گذارید و ما صفحه را بررسی می‌کنیم."),
      ),
    ),
    wordpressFields,
    h("div", { class: "step-actions" }, save),
  );
  box.append(form);
  return box;
}

function removeSite(site, context) {
  const problem = problemBox();
  const holder = h("div", { class: "step-actions" });
  const ask = h("button", {
    class: "btn btn-danger", type: "button", text: "حذف این سایت",
    onclick: () => {
      const yes = h("button", {
        class: "btn btn-danger", type: "button", text: "بله، حذف کن",
        onclick: async () => {
          yes.disabled = true;
          problem.hide();
          try {
            await api(`/v1/sites/${encodeURIComponent(site.site_id)}`, { method: "DELETE" });
            context.state.siteId = null;
            await context.refreshSites();
            context.rerender();
          } catch (error) {
            problem.show(error);
            yes.disabled = false;
          }
        },
      });
      const no = h("button", { class: "btn", type: "button", text: "نه", onclick: () => holder.replaceChildren(ask) });
      holder.replaceChildren(
        h("span", { class: "small", text: "این سایت از داشبورد حذف شود؟ تغییرهایی که قبلاً روی سایت اعمال شده‌اند سر جایشان می‌مانند." }),
        yes, no,
      );
    },
  });
  holder.append(ask);
  return h("div", {}, problem, holder);
}

function syncStep(sync) {
  if (!sync) return h("p", { class: "small muted", text: "وضعیت در دسترس نیست." });
  const box = h("div", {});
  const text = {
    up_to_date: "کامل و به‌روز است.",
    backfilling: `در حال دریافت: ${fmt.int(sync.synced_days)} روز از ${fmt.int(sync.days_in_window)} روز.`,
    failing: "گوگل به درخواست‌های اخیر جواب نداده است؛ دوباره تلاش می‌شود.",
    not_connected: "بعد از وصل شدن سرچ کنسول شروع می‌شود.",
    not_eligible: "property باید متعلق به دامنهٔ همین سایت باشد؛ حساب گوگل خودتان را وصل کنید و دوباره انتخابش کنید.",
  }[sync.state] || "";
  box.append(h("p", { class: "small muted", text }));
  if (sync.state === "backfilling") {
    box.append(h("div", { class: "progress", role: "progressbar", "aria-valuenow": Math.round(sync.progress * 100), "aria-valuemin": 0, "aria-valuemax": 100 },
      h("span", { style: `width:${Math.round(sync.progress * 100)}%` })));
  }
  return box;
}

export async function settingsPage(container, context) {
  const { site, state, alive } = context;
  const header = h("div", { class: "page-header" },
    h("div", {},
      h("h1", { class: "page-title", text: "تنظیمات پروژه" }),
      h("p", { class: "page-sub", text: "اتصال‌های هر سایت را این‌جا کامل کنید." }),
    ),
  );

  if (!site) {
    container.append(header, h("div", { class: "settings-grid" }, addSiteForm({ ...context, first: true })));
    return;
  }

  const [google, sync] = await Promise.all([
    googleStep(context),
    api(`/v1/sites/${encodeURIComponent(site.site_id)}/sync`).catch(() => null),
  ]);
  if (!alive()) return;

  const syncDone = sync?.state === "up_to_date";
  const progress = h("section", { class: "card-panel" },
    h("div", { class: "panel-title" },
      h("span", { text: site.name }),
      h("span", { class: "badge ltr", text: new URL(site.base_url).host }),
    ),
    step(1, google.connected, "وصل کردن حساب گوگل", google.node),
    step(2, site.gsc_configured, "انتخاب property سرچ کنسول", propertyStep(site, google.connected, context)),
    step(3, Boolean(site.site_adapter_type), "روش اعمال تغییرها روی سایت", siteConnectionStep(site, context)),
    step(4, syncDone, "دریافت تاریخچهٔ سرچ کنسول", syncStep(sync)),
    removeSite(site, context),
  );

  container.append(header, h("div", { class: "settings-grid" }, progress, addSiteForm({ ...context, first: state.sites.length === 0 })));
}
