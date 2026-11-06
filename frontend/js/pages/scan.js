// بررسی سایت: what a search engine finds there, and robots.txt and the sitemap made right.
// Needs nothing but the site's address, so it is where a brand-new site starts.

import { api, messageFor } from "../api.js";
import { h, clear, fmt, readableUrl } from "../dom.js";
import { FILE_CHANGES, FILE_NOTES, issueLabel } from "../labels.js";
import { noSiteYet } from "./dashboard.js";
import { targetsPanel } from "./targets.js";

const FILE_TITLES = { robots: "فایل robots.txt", sitemap: "نقشهٔ سایت (sitemap)" };
const WATCH_SECONDS = [2, 3, 5, 5, 10, 10, 20, 30, 40];
const SCAN_LOST_AFTER_MS = 15 * 60 * 1000;

function filePanel(change, { base, act, problem }) {
  const path = `${base}/site-files/${change.kind}`;
  const buttons = h("div", { class: "approval-actions" });
  const button = (text, verb, primary = false) => {
    const node = h("button", {
      class: `btn${primary ? " btn-primary" : ""}`, type: "button", text,
      onclick: () => act(node, `${path}/${verb}`),
    });
    return node;
  };
  const download = h("a", {
    class: "btn", href: `${path}/content`, download: change.kind === "robots" ? "robots.txt" : "sitemap.xml",
    text: "دریافت فایل",
  });
  const body = [];
  const preview = () => h("details", { class: "small" },
    h("summary", { class: "muted", text: "متن فایل" }),
    h("pre", { class: "file-text ltr", text: change.proposed_text }));

  const says = change.changes.map((code) => FILE_CHANGES[code]).filter(Boolean);
  if (says.length) body.push(h("ul", { class: "file-changes" }, says.map((text) => h("li", { text }))));
  const dropped = change.dropped || [];
  if (dropped.length && change.status !== "applied") {
    body.push(h("details", { class: "small" },
      h("summary", { class: "muted", text: `${fmt.int(dropped.length)} صفحه از نقشه بیرون می‌رود` }),
      h("ul", { class: "issue-urls" }, dropped.slice(0, 50).map((url) => h("li", { class: "ltr", text: url })))));
  }
  const removesBlock = change.changes.includes("removed_block_all");
  if (removesBlock && change.status === "proposed") {
    body.push(h("div", { class: "notice notice-warn", text: "الان این فایل گوگل را از کل سایت دور نگه می‌دارد. اگر سایت عمداً بسته است (مثلاً نسخهٔ آزمایشی)، این پیشنهاد را رد کنید." }));
  }

  let label = { text: "پیشنهاد", tone: "warn" };
  if (change.status === "proposed" || change.status === "rejected") {
    if (change.status === "rejected") label = { text: "رد شده", tone: "" };
    if (change.previous_text) {
      body.push(h("details", { class: "small" },
        h("summary", { class: "muted", text: "متن فعلی روی سایت" }),
        h("pre", { class: "file-text ltr", text: change.previous_text })));
    }
    body.push(preview());
    buttons.append(button("تأیید و گرفتن فایل", "approve", true));
    if (change.status !== "rejected") buttons.append(button("رد کردن", "reject"));
  } else if (change.status === "waiting_for_customer") {
    label = { text: "در انتظار شما", tone: "warn" };
    const note = FILE_NOTES[change.note];
    if (note) body.push(h("div", { class: "notice notice-warn", text: note }));
    body.push(h("div", { class: "apply-box" },
      "این فایل را بگیرید و طوری در سایتتان بگذارید که در این آدرس باز شود:",
      h("div", { class: "apply-title ltr", text: change.public_url }),
    ));
    if (change.kind === "sitemap") {
      body.push(h("p", { class: "small muted", text: "این فایل فهرست صفحه‌های امروز است و خودش به‌روز نمی‌شود؛ بعد از اضافه شدن صفحه‌های تازه، سایت را دوباره بررسی کنید." }));
    }
    if (change.changes.includes("partial")) {
      body.push(h("p", { class: "small muted", text: "سایت بیشتر از سقف یک بررسی صفحه دارد، پس این فهرست کامل نیست." }));
    }
    if (change.previous_text) {
      body.push(h("details", { class: "small" },
        h("summary", { class: "muted", text: "متن فعلی روی سایت، اگر خواستید برگردانید" }),
        h("pre", { class: "file-text ltr", text: change.previous_text })));
    }
    body.push(preview());
    buttons.append(button("گذاشتم، بررسی کن", "check", true), download, button("منصرف شدم", "reject"));
  } else if (change.status === "applied") {
    label = { text: "روی سایت است", tone: "good" };
    body.push(h("p", { class: "small" }, "این فایل در این آدرس دیده شد: ", h("span", { class: "ltr", text: change.public_url })));
    if (change.kind === "sitemap") {
      body.push(h("p", { class: "small muted", text: "قدم آخر: این آدرس را در بخش Sitemaps سرچ کنسول هم ثبت کنید تا گوگل زودتر آن را ببیند." }));
    }
    if (change.previous_text) {
      body.push(h("details", { class: "small" },
        h("summary", { class: "muted", text: "متن قبلی، اگر خواستید برگردانید" }),
        h("pre", { class: "file-text ltr", text: change.previous_text })));
    }
  }

  return h("article", { class: "approval-item", id: `file-${change.kind}` },
    h("div", { class: "approval-header" },
      h("span", { text: FILE_TITLES[change.kind] }),
      h("span", { class: `badge ${label.tone ? `badge-${label.tone}` : ""}`, text: label.text }),
    ),
    problem(change.kind),
    body,
    buttons.hasChildNodes() ? buttons : null,
  );
}

// The middle page's time: one slow page does not make the site slow.
function medianMs(pages) {
  const times = pages.map((page) => page.speed?.fetch_ms).filter((ms) => typeof ms === "number").sort((a, b) => a - b);
  return times.length ? times[Math.floor(times.length / 2)] : null;
}

function issueGroups(issues) {
  const groups = new Map();
  for (const issue of issues) {
    if (!groups.has(issue.code)) groups.set(issue.code, []);
    groups.get(issue.code).push(issue);
  }
  return [...groups.entries()];
}

// Problems a written description fixes: each such page gets a button that asks for one.
const DESCRIPTION_CODES = new Set(["missing_description", "description_too_long", "duplicate_meta_description"]);

function askButton(url, askDescription) {
  const holder = h("span", {});
  const button = h("button", {
    class: "btn-link", type: "button", text: "پیشنهاد توضیحات متا",
    onclick: async () => {
      button.disabled = true;
      const problem = await askDescription(url);
      holder.replaceChildren(problem
        ? h("span", { class: "small", text: problem })
        : h("a", { class: "small", href: "#/inbox", text: "پیشنهاد تا یکی دو دقیقهٔ دیگر در صندوق تأیید آماده می‌شود" }));
    },
  });
  holder.append(button);
  return holder;
}

function issueList(issues, { askDescription } = {}) {
  if (issues.length === 0) return h("div", { class: "empty", text: "مشکلی پیدا نشد." });
  return issueGroups(issues).map(([code, items]) => {
    const info = issueLabel(code);
    const urls = items.map((item) => item.url).filter(Boolean);
    return h("div", { class: `issue issue-${items[0].severity}` },
      h("div", { class: "issue-title" },
        h("span", { text: info.title }),
        urls.length ? h("span", { class: "badge", text: `${fmt.int(urls.length)} صفحه` }) : null,
      ),
      h("p", { class: "small muted", text: info.fix }),
      urls.length
        ? h("details", { class: "small" },
            h("summary", { class: "muted", text: "صفحه‌ها" }),
            h("ul", { class: "issue-urls" }, urls.slice(0, 50).map((url) => h("li", {},
              h("a", { class: "page-url", href: url, target: "_blank", rel: "noopener noreferrer", title: url, text: readableUrl(url) }),
              askDescription && DESCRIPTION_CODES.has(code) ? [" ", askButton(url, askDescription)] : null))))
        : null,
    );
  });
}

export async function scanPage(container, { site, alive }) {
  if (!site) {
    container.append(noSiteYet());
    return;
  }
  const base = `/v1/sites/${encodeURIComponent(site.site_id)}`;
  const area = h("div", {});
  // Outside the area redrawn while a scan runs, so what is typed there stays;
  // built again only for a new scan, whose pages the words are for.
  const targets = h("div", {});
  let targetsFor = null;
  const top = h("div", { class: "hidden", role: "alert" });
  const problems = new Map();
  let timer = null;
  let watching = 0;

  const start = h("button", {
    class: "btn btn-primary", type: "button", text: "بررسی سایت",
    onclick: async () => {
      start.disabled = true;
      top.className = "hidden";
      try {
        await api(`${base}/scans`, { method: "POST", body: {} });
        watch();
      } catch (error) {
        top.className = "notice notice-error";
        top.textContent = messageFor(error);
        start.disabled = false;
      }
    },
  });

  const problem = (kind) => {
    const box = h("div", { class: "notice notice-error hidden", role: "alert" });
    const text = problems.get(kind);
    if (text) {
      box.textContent = text;
      box.classList.remove("hidden");
    }
    return box;
  };

  const act = async (button, path) => {
    button.disabled = true;
    const kind = path.split("/site-files/")[1].split("/")[0];
    problems.delete(kind);
    try {
      await api(path, { method: "POST", body: {} });
      watch();
    } catch (error) {
      problems.set(kind, messageFor(error));
      await draw();
    }
  };

  let generation = 0;

  // A description for one page, written by the model and waiting in the inbox. Null when asked.
  const askDescription = async (url) => {
    try {
      await api(`${base}/meta-descriptions`, { method: "POST", body: { url } });
      return null;
    } catch (error) {
      return messageFor(error);
    }
  };

  function watch() {
    watching = 0;
    clearTimeout(timer);
    tick(++generation);
  }

  // One chain of looks at a time: a look still on its way when a new one starts
  // ends where it lands, instead of carrying on beside it.
  async function tick(mine = generation) {
    if (!alive() || mine !== generation) return;
    const busy = await draw();
    if (!alive() || mine !== generation) return;
    if (!busy && watching > 0) return;
    const wait = WATCH_SECONDS[Math.min(watching, WATCH_SECONDS.length - 1)];
    watching += 1;
    if (watching <= 30) timer = setTimeout(() => tick(mine), wait * 1000);
  }

  // True while something is still on its way.
  async function draw() {
    let scan = null;
    let files = [];
    try {
      [scan, files] = await Promise.all([
        api(`${base}/scans/latest`).catch((error) => { if (error.status === 404) return null; throw error; }),
        api(`${base}/site-files`),
      ]);
    } catch (error) {
      if (alive()) {
        top.className = "notice notice-error";
        top.textContent = messageFor(error);
      }
      return false;
    }
    if (!alive()) return false;
    top.className = "hidden";
    // Older than this, the job was lost; the server allows a new scan by then.
    const fresh = scan && Date.now() - new Date(scan.requested_at).getTime() < SCAN_LOST_AFTER_MS;
    const unfinished = scan && (scan.status === "queued" || scan.status === "running");
    const running = unfinished && fresh;
    start.disabled = Boolean(running);
    start.textContent = scan ? "بررسی دوباره" : "بررسی سایت";
    clear(area);
    if (!scan) {
      area.append(h("section", { class: "card-panel empty" },
        h("p", { text: "این بررسی فقط آدرس سایت را لازم دارد: robots.txt، نقشهٔ سایت و صفحه‌ها را مثل یک موتور جستجو می‌خوانیم و می‌گوییم چه چیزی جلوی دیده شدن سایت را گرفته است." }),
      ));
      return false;
    }
    // Whatever the scan is doing: the way back from a change must stay in reach.
    const shownFiles = files.filter((change) => change.status !== "not_needed");
    const filesPanel = shownFiles.length
      ? h("section", { class: "card-panel" },
          h("div", { class: "panel-title", text: "robots.txt و نقشهٔ سایت" }),
          shownFiles.map((change) => filePanel(change, { base, act, problem })),
        )
      : null;
    if (running) {
      area.append(h("section", { class: "card-panel empty", role: "status", text: "در حال خواندن سایت… معمولاً یکی دو دقیقه طول می‌کشد." }));
      if (filesPanel) area.append(filesPanel);
      return true;
    }
    if (scan.status === "failed" || unfinished) {
      area.append(h("div", { class: "notice notice-error", text: "این بررسی کامل نشد. کمی بعد دوباره امتحان کنید." }));
      if (filesPanel) area.append(filesPanel);
      return false;
    }

    const count = (severity) => scan.issues.filter((issue) => issue.severity === severity).length;
    const usual = medianMs(scan.pages);
    const fact = (label, value) => h("div", { class: "measure-cell" },
      h("div", { class: "measure-label", text: label }), h("div", { class: "measure-value", text: value }));
    area.append(
      h("section", { class: "card-panel" },
        h("div", { class: "panel-title" },
          h("span", { text: "خلاصه" }),
          h("span", { class: "small muted", text: fmt.moment(scan.completed_at) }),
        ),
        h("div", { class: "measure" },
          scan.health_score !== null && scan.health_score !== undefined
            ? fact("امتیاز سلامت", `${fmt.int(scan.health_score)} از ۱۰۰`)
            : null,
          fact("صفحهٔ خوانده‌شده", fmt.int(scan.pages.filter((page) => page.status_code !== null).length)),
          fact("مشکل جدی", fmt.int(count("critical"))),
          fact("قابل بهبود", fmt.int(count("warning"))),
          usual === null ? null : fact("زمان پاسخ معمول (از سرور ما)", `${fmt.pos(usual / 1000)} ثانیه`),
          fact("نقشهٔ سایت", scan.sitemap_urls.length ? `${fmt.int(scan.sitemap_page_count)} صفحه` : "ندارد"),
          fact("robots.txt", scan.robots_text !== null ? "دارد" : "ندارد"),
        ),
        scan.pages_capped
          ? h("p", { class: "small muted", text: "سایت بیشتر از سقف یک بررسی صفحه داشت؛ این نتیجه همهٔ صفحه‌ها را پوشش نمی‌دهد." })
          : null,
      ),
    );
    if (filesPanel) area.append(filesPanel);
    area.append(h("section", { class: "card-panel" },
      h("div", { class: "panel-title", text: "چیزهایی که پیدا شد" }),
      issueList(scan.issues, { askDescription }),
    ));
    if (targetsFor !== scan.completed_at) {
      targetsFor = scan.completed_at;
      clear(targets);
      targets.append(targetsPanel({ site, alive }));
    }
    return false;
  }

  container.append(
    h("div", { class: "page-header" },
      h("div", {},
        h("h1", { class: "page-title", text: "بررسی سایت" }),
        h("p", { class: "page-sub ltr", text: new URL(site.base_url).host }),
      ),
      start,
    ),
    top, area, targets,
  );
  await tick();
}
