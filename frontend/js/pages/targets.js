// کلمه‌های هدف: for a site with no Search Console data, the words each page should be found by,
// a title proposed for one, and where the page stands in Google week by week.

import { api, messageFor } from "../api.js";
import { h, clear, fmt, readableUrl } from "../dom.js";

const WATCH_SECONDS = [2, 3, 5, 5, 10, 10, 20];
const RUN_TRIES = 60;

function positionText(item) {
  const last = item.checks.at(-1);
  if (!last) return "هنوز بررسی نشده";
  if (last.position === null) return "در ۱۰ نتیجهٔ اول نیست";
  return `جایگاه ${fmt.int(last.position)}`;
}

function trendText(item) {
  if (item.checks.length < 2) return null;
  const [before, now] = item.checks.slice(-2).map((check) => check.position);
  if (before === now) return null;
  if (now === null) return "از ۱۰ نتیجهٔ اول بیرون رفت";
  if (before === null) return "وارد ۱۰ نتیجهٔ اول شد";
  return now < before ? `${fmt.int(before - now)} رتبه بالاتر از هفتهٔ قبل` : `${fmt.int(now - before)} رتبه پایین‌تر از هفتهٔ قبل`;
}

// Why no title came, in the customer's terms: the reasons are the SERP check's own.
function blockedText(reasons) {
  if (reasons.some((reason) => reason.startsWith("serp_unavailable"))) {
    return "الان به نتایج گوگل دسترسی نداشتیم. کمی بعد دوباره امتحان کنید.";
  }
  if (reasons.includes("target_title_missing")) {
    return "عنوان فعلی این صفحه خوانده نشد. مطمئن شوید صفحه باز می‌شود و عنوان دارد، بعد دوباره امتحان کنید.";
  }
  return "برای این کلمه رقیب کافی در نتایج گوگل نبود که عنوان را با آن‌ها بسنجیم. کلمهٔ دیگری امتحان کنید.";
}

export function targetsPanel({ site, alive }) {
  const base = `/v1/sites/${encodeURIComponent(site.site_id)}`;
  const section = h("section", { class: "card-panel" });
  const notice = h("div", { class: "hidden", role: "status" });
  let timer = null;
  let watching = 0;
  let data = null;
  // The pages a word can be for come with the first look; later answers are the list alone.
  let pages = [];

  const say = (text, tone = "") => {
    notice.className = `notice${tone ? ` notice-${tone}` : ""}`;
    notice.textContent = text;
  };

  const call = async (button, path, options) => {
    if (button) button.disabled = true;
    try {
      const answer = await api(path, options);
      return answer;
    } catch (error) {
      say(messageFor(error), "error");
      if (button) button.disabled = false;
      return null;
    }
  };

  async function load() {
    try {
      data = await api(`${base}/target-keywords`);
      pages = data.pages || pages;
    } catch (error) {
      if (alive()) say(messageFor(error), "error");
      return;
    }
    if (alive()) draw();
  }

  function watch() {
    watching = 0;
    clearTimeout(timer);
    tick();
  }

  async function tick() {
    await load();
    if (!alive() || data?.suggestion_status !== "running") return;
    timer = setTimeout(tick, WATCH_SECONDS[Math.min(watching++, WATCH_SECONDS.length - 1)] * 1000);
  }

  async function followRun(runId, holder) {
    for (let attempt = 0; attempt < RUN_TRIES; attempt += 1) {
      await new Promise((resolve) => { setTimeout(resolve, 2000); });
      if (!alive()) return;
      let run;
      try {
        run = await api(`/v1/runs/${encodeURIComponent(runId)}`);
      } catch {
        continue;
      }
      if (run.status === "completed") {
        const result = run.result || {};
        clear(holder);
        if (result.action_id) {
          holder.append(h("a", { href: `#/inbox/${encodeURIComponent(result.action_id)}`, text: "عنوان پیشنهادی آماده است؛ در صندوق تأیید ببینید" }));
        } else if (result.title_proposal_status === "blocked") {
          holder.textContent = blockedText(result.title_proposal_reasons || []);
        } else {
          holder.textContent = "مدل زبانی عنوان قابل‌قبولی نداد. کمی بعد دوباره امتحان کنید.";
        }
        return;
      }
      if (run.status === "failed" && attempt > 5) {
        holder.textContent = "پیشنهاد عنوان انجام نشد. کمی بعد دوباره امتحان کنید.";
        return;
      }
    }
    holder.textContent = "بیشتر از معمول طول کشید. کمی بعد صفحه را تازه کنید؛ اگر عنوانی آماده شده باشد، در صندوق تأیید هست.";
  }

  function row(item) {
    const result = h("div", { class: "small muted" });
    const actions = h("div", { class: "approval-actions" });
    const status = (value, text) => {
      const node = h("button", {
        class: "btn", type: "button", text,
        onclick: async () => {
          const answer = await call(node, `${base}/target-keywords/status`, { method: "POST", body: { keyword: item.keyword, status: value } });
          if (answer && alive()) { data = answer; draw(); }
        },
      });
      return node;
    };
    if (item.status === "suggested") {
      actions.append(status("confirmed", "نگه داشتن"), status("dismissed", "لازم نیست"));
    } else {
      const title = h("button", {
        class: "btn btn-primary", type: "button", text: "پیشنهاد عنوان",
        onclick: async () => {
          const run = await call(title, `${base}/target-keywords/title`, { method: "POST", body: { keyword: item.keyword } });
          if (!run) return;
          result.textContent = "در حال مقایسه با نتایج گوگل و نوشتن عنوان… معمولاً کمتر از یک دقیقه.";
          await followRun(run.run_id, result);
          title.disabled = false;
        },
      });
      actions.append(title, status("dismissed", "حذف"));
    }
    const trend = trendText(item);
    return h("div", { class: "target-row" },
      h("div", { class: "target-word" },
        h("strong", { text: item.keyword }),
        item.status === "suggested" ? h("span", { class: "badge badge-warn", text: "پیشنهاد" }) : null,
      ),
      h("a", { class: "page-url small", href: item.url, target: "_blank", rel: "noopener noreferrer", title: item.url, text: readableUrl(item.url) }),
      item.reason ? h("div", { class: "small muted", text: item.reason }) : null,
      item.status === "confirmed"
        ? h("div", { class: "small" }, positionText(item), trend ? h("span", { class: "muted", text: ` · ${trend}` }) : null)
        : null,
      actions,
      result,
    );
  }

  // The fields as last drawn: a redraw while polling must not wipe what is being typed.
  let fields = null;

  function draw() {
    const typed = fields && {
      description: fields.description.value, seeds: fields.seeds.value, word: fields.word.value, page: fields.page.value,
    };
    clear(section);
    const running = data.suggestion_status === "running";
    const description = h("textarea", {
      class: "input", maxLength: 1000, rows: 3, value: typed ? typed.description : data.description,
      placeholder: "مثلاً: فروشگاه اینترنتی کفش ورزشی در تهران، با ارسال به سراسر ایران",
      "aria-label": "کسب‌وکارتان چیست؟",
    });
    const seeds = h("input", {
      class: "input", maxLength: 400, value: typed ? typed.seeds : data.seeds.join("، "),
      placeholder: "کلماتی که در ذهن دارید، با ویرگول جدا کنید",
      "aria-label": "کلماتی که در ذهن دارید",
    });
    const suggest = h("button", {
      class: "btn btn-primary", type: "button", disabled: running,
      text: running ? "در حال پیشنهاد…" : "پیشنهاد کلمه برای صفحه‌ها",
      onclick: async () => {
        notice.className = "hidden";
        const words = seeds.value.split(/[,،\n]/).map((word) => word.trim()).filter(Boolean).slice(0, 10);
        const saved = await call(suggest, `${base}/target-keywords/brief`, { method: "PUT", body: { description: description.value, seeds: words } });
        if (!saved) return;
        const started = await call(suggest, `${base}/target-keywords/suggest`, { method: "POST", body: {} });
        if (started) watch();
      },
    });

    const keep = data.keywords.filter((item) => item.status !== "dismissed");
    const word = h("input", { class: "input", maxLength: 80, value: typed ? typed.word : "", placeholder: "کلمهٔ دلخواه", "aria-label": "کلمهٔ دلخواه" });
    const page = h("select", { class: "input", "aria-label": "برای کدام صفحه" },
      pages.map((item) => h("option", { value: item.url, text: item.title || readableUrl(item.url) })));
    if (typed && pages.some((item) => item.url === typed.page)) page.value = typed.page;
    fields = { description, seeds, word, page };
    const add = h("button", {
      class: "btn", type: "button", text: "افزودن",
      onclick: async () => {
        if (!word.value.trim()) { word.focus(); return; }
        const answer = await call(add, `${base}/target-keywords`, { method: "POST", body: { keyword: word.value, url: page.value } });
        // Added: the box is emptied for the next word.
        if (answer && alive()) { word.value = ""; data = answer; draw(); }
      },
    });

    // The DOM's own append writes null as text; what is not there is left out first.
    section.append(...[
      h("div", { class: "panel-title", text: "کلمه‌های هدف" }),
      h("p", { class: "small muted", text: "سایت تازه است و هنوز داده‌ای در سرچ کنسول ندارد؟ از همین‌جا شروع کنید. بگویید کسب‌وکارتان چیست تا برای هر صفحه کلمه‌ای پیشنهاد کنیم که مردم در گوگل جستجو می‌کنند. جایگاه کلمه‌هایی را که نگه دارید هر هفته در گوگل بررسی می‌کنیم." }),
      notice,
      h("div", { class: "field" }, h("label", { text: "کسب‌وکارتان چیست؟" }), description),
      h("div", { class: "field" }, h("label", { text: "کلماتی که در ذهن دارید (اختیاری)" }), seeds),
      h("div", { class: "approval-actions" }, suggest),
      data.suggestion_status === "failed"
        ? h("div", { class: "notice notice-warn", text: "پیشنهاد کلمه انجام نشد. کمی بعد دوباره امتحان کنید." })
        : null,
      keep.length ? h("div", { class: "target-list" }, keep.map(row)) : null,
      pages.length
        ? h("div", { class: "target-add" }, h("div", { class: "small", text: "کلمهٔ خودتان را اضافه کنید:" }), word, page, add)
        : null,
    ].filter(Boolean));
  }

  // Opened while a suggestion is on its way: follow it, as after a click.
  tick();
  return section;
}
