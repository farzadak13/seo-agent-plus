// عملکرد سایت: Search Console history from the warehouse, and where an analysis starts.

import { api, messageFor } from "../api.js";
import { h, clear, fmt, readableUrl, isoDaysBefore } from "../dom.js";
import { renderChart } from "../chart.js";
import { noSiteYet } from "./dashboard.js";

const RANGES = [
  { days: 7, label: "۷ روز" },
  { days: 28, label: "۲۸ روز" },
  { days: 90, label: "۳ ماه" },
  { days: 480, label: "۱۶ ماه", interval: "week" },
];
const DEVICES = [
  { value: null, label: "همه دستگاه‌ها" },
  { value: "MOBILE", label: "موبایل" },
  { value: "DESKTOP", label: "دسکتاپ" },
  { value: "TABLET", label: "تبلت" },
];
const METRICS = [
  { key: "clicks", label: "کلیک‌ها", format: fmt.int },
  { key: "impressions", label: "نمایش", format: fmt.int },
  { key: "ctr", label: "نرخ کلیک (CTR)", format: fmt.pct },
  { key: "position", label: "میانگین جایگاه", format: fmt.pos, invert: true },
];
const PAGE_SIZE = 50;
const RUN_WINDOW_DAYS = 28;

function trendCell(row, report) {
  const complete = report.previous_days_covered !== null && report.previous_days_covered >= report.previous_days_in_range;
  if (!complete) {
    return h("span", { class: "trend-flat", title: "دادهٔ دورهٔ قبل هنوز کامل نیست", text: "—" });
  }
  const before = row.previous?.clicks;
  const now = row.current.clicks;
  if (before === undefined || before === null || before === 0) {
    return now > 0 ? h("span", { class: "trend-up", text: "تازه" }) : h("span", { class: "trend-flat", text: "—" });
  }
  const change = (now - before) / before;
  if (Math.abs(change) < 0.0005) return h("span", { class: "trend-flat", text: "بدون تغییر" });
  return h("span", { class: change > 0 ? "trend-up" : "trend-down", text: `${change > 0 ? "↑" : "↓"} ${fmt.pct(Math.abs(change))}` });
}

export async function gscPage(container, { site, alive, refreshCount }) {
  if (!site) {
    container.append(noSiteYet());
    return;
  }
  if (!site.gsc_configured) {
    container.append(h("div", { class: "card-panel empty" },
      h("p", { text: "سرچ کنسول این سایت هنوز وصل نیست." }),
      h("a", { class: "btn btn-primary", href: "#/settings", text: "وصل کردن سرچ کنسول" }),
    ));
    return;
  }

  const base = `/v1/sites/${encodeURIComponent(site.site_id)}`;
  const sync = await api(`${base}/sync`).catch(() => null);
  if (!alive()) return;
  if (sync?.state === "not_eligible") {
    // The server would refuse every report; say why instead of drawing an empty page.
    container.append(h("div", { class: "card-panel empty" },
      h("p", { text: "دادهٔ سرچ کنسول این سایت خوانده نمی‌شود، چون property آن باید متعلق به دامنهٔ خودش باشد. در تنظیمات پروژه حساب گوگل خودتان را وصل کنید و property درست را انتخاب کنید." }),
      h("a", { class: "btn btn-primary", href: "#/settings", text: "رفتن به تنظیمات پروژه" }),
    ));
    return;
  }

  const view = {
    days: 28, device: null, metric: "clicks", tab: "queries", page: null,
    sort: "clicks", order: "desc", contains: "", rows: [], report: null, nextOffset: null,
  };
  const syncNote = h("div", { class: "notice notice-warn hidden" });
  const kpis = h("div", { class: "kpis" });
  const chart = h("div", { class: "chart" });
  const chartNote = h("div", { class: "chart-note hidden" });
  const tableArea = h("div", {});
  const runNotice = h("div", { class: "hidden", role: "status" });
  const loadNotice = h("div", { class: "hidden", role: "alert" });
  let performance = null;
  let endDay = null;
  let loadToken = 0;
  let searchTimer = null;
  // `view` is what is on screen. What was asked for waits here until it has
  // arrived, so a failed or overtaken request leaves the rows and their buttons
  // matching the view.
  let pending = {};
  let chartPending = false;

  const range = (state) => (endDay ? { start: isoDaysBefore(endDay, state.days - 1), end: endDay } : {});

  function drawChart() {
    if (!performance) return;
    const metric = METRICS.find((item) => item.key === view.metric);
    const interval = performance.interval;
    const length = interval === "week" ? 7 : interval === "month" ? 30 : 1;
    renderChart(chart, performance.series.map((point) => ({
      date: point.period_start,
      value: point[metric.key],
      partial: point.days_covered < length,
    })), { format: metric.format, label: metric.label, invert: metric.invert });
  }

  function drawKpis() {
    clear(kpis);
    for (const metric of METRICS) {
      kpis.append(h("button", {
        class: "kpi", type: "button", "aria-pressed": String(view.metric === metric.key),
        onclick: () => { view.metric = metric.key; drawKpis(); drawChart(); },
      },
        h("span", { class: "kpi-label", text: metric.label }),
        h("span", { class: "kpi-value", text: performance ? metric.format(performance.totals[metric.key]) : "…" }),
      ));
    }
  }

  function showPerformance(report) {
    performance = report;
    const missing = performance.days_in_range - performance.days_covered;
    chartNote.textContent = missing > 0
      ? `دادهٔ ${fmt.int(missing)} روز از این بازه هنوز دریافت نشده است؛ عددها فقط روزهای موجود را نشان می‌دهند.`
      : "";
    chartNote.classList.toggle("hidden", missing <= 0);
    drawKpis();
    drawChart();
  }

  function sortHeader(key, label) {
    const sorted = view.sort === key;
    return h("th", { "aria-sort": sorted ? (view.order === "asc" ? "ascending" : "descending") : null },
      h("button", {
        class: sorted ? "sorted" : "", type: "button",
        text: `${label}${sorted ? (view.order === "asc" ? " ▲" : " ▼") : ""}`,
        onclick: () => {
          // On top of a click whose answer has not arrived yet.
          const { sort, order } = { ...view, ...pending };
          load({
            changes: sort === key
              ? { order: order === "asc" ? "desc" : "asc" }
              : { sort: key, order: key === "position" ? "asc" : "desc" },
          });
        },
      }));
  }

  async function startRun(query, button) {
    button.disabled = true;
    button.textContent = "در حال تحلیل…";
    runNotice.className = "notice";
    runNotice.textContent = `در حال تحلیل «${query}»: داده‌ها بررسی و عنوان رقبا مقایسه می‌شود. معمولاً کمتر از یک دقیقه طول می‌کشد.`;
    runNotice.scrollIntoView?.({ block: "nearest" });
    try {
      const run = await api(`${base}/runs`, {
        method: "POST",
        body: {
          start_date: isoDaysBefore(endDay, RUN_WINDOW_DAYS - 1), end_date: endDay,
          normalized_url: view.page, normalized_query: query, candidate_id: "dashboard",
        },
      });
      await followRun(run.run_id, query);
    } catch (error) {
      runNotice.className = "notice notice-error";
      runNotice.textContent = messageFor(error);
    }
    button.disabled = false;
    button.textContent = "تحلیل و پیشنهاد عنوان";
  }

  async function followRun(runId, query) {
    // A run is "failed" between a job's attempts too, so one sighting says little.
    let failures = 0;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => { setTimeout(resolve, 2000); });
      if (!alive()) return;
      const run = await api(`/v1/runs/${encodeURIComponent(runId)}`);
      failures = run.status === "failed" ? failures + 1 : 0;
      if (failures >= 5 || run.status === "cancelled") {
        runNotice.className = "notice notice-error";
        runNotice.textContent = "تحلیل کامل نشد. کمی بعد دوباره امتحان کنید.";
        return;
      }
      if (run.status === "completed") {
        const result = run.result || {};
        clear(runNotice);
        if (result.action_id) {
          runNotice.className = "notice notice-good";
          runNotice.append(`برای «${query}» عنوان تازه پیشنهاد شد. `,
            h("a", { href: `#/inbox/${encodeURIComponent(result.action_id)}`, text: "مشاهده و تصمیم در صندوق تأیید" }));
          refreshCount();
        } else if (result.title_proposal_status === "blocked") {
          runNotice.className = "notice notice-warn";
          runNotice.textContent = "مقایسه با نتایج گوگل نشان داد الان نمی‌شود با اطمینان عنوان تازه‌ای پیشنهاد کرد؛ مثلاً این صفحه در ۱۰ نتیجهٔ اول این کلمه نیست، یا منبع نتایج گوگل در دسترس نبود.";
        } else if (result.title_proposal_status === "failed") {
          runNotice.className = "notice notice-warn";
          runNotice.textContent = "مدل زبانی پیشنهاد قابل‌قبولی نداد. کمی بعد دوباره امتحان کنید.";
        } else {
          runNotice.className = "notice";
          runNotice.textContent = "تحلیل انجام شد. برای این صفحه و کلمه الان تغییر عنوان پیشنهاد نمی‌شود.";
        }
        return;
      }
    }
    runNotice.className = "notice";
    runNotice.textContent = "تحلیل بیشتر از معمول طول کشیده است. نتیجه بعداً در صندوق تأیید دیده می‌شود.";
  }

  function row(item) {
    const report = view.report;
    const isPageRow = view.tab === "pages";
    const first = isPageRow
      ? h("td", { class: "page-cell", title: item.key },
          h("button", {
            class: "btn-link", type: "button", text: readableUrl(item.key),
            onclick: () => load({ changes: { page: item.key, tab: "queries", contains: "" } }),
          }))
      : h("td", { class: "query-cell", text: item.key });
    let action = null;
    if (!isPageRow && view.page) {
      const button = h("button", {
        class: "btn", type: "button", text: "تحلیل و پیشنهاد عنوان",
        onclick: () => startRun(item.key, button),
      });
      action = h("td", {}, button);
    }
    return h("tr", {},
      first,
      h("td", { text: fmt.int(item.current.clicks) }),
      h("td", { text: fmt.int(item.current.impressions) }),
      h("td", { text: fmt.pct(item.current.ctr) }),
      h("td", { text: fmt.pos(item.current.position) }),
      isPageRow ? null : h("td", { text: fmt.int(item.search_volume) }),
      h("td", {}, trendCell(item, report)),
      action,
    );
  }

  function drawTable() {
    clear(tableArea);
    const isPages = view.tab === "pages";
    const search = h("input", {
      class: "search-box", type: "search", value: view.contains, "aria-label": "جستجو در جدول",
      placeholder: isPages ? "جستجوی آدرس صفحه…" : "جستجوی کلمه…",
      oninput: (event) => {
        pending.contains = event.target.value;
        loadToken += 1;  // whatever is on its way is for the words before these
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => load({ keepFocus: true }), 350);
      },
    });
    const tabs = h("div", { class: "filters-group" },
      [["queries", "کلمات کلیدی"], ["pages", "صفحه‌ها"]].map(([id, label]) => h("button", {
        class: `filter-btn${view.tab === id ? " active" : ""}`, type: "button", text: label,
        onclick: () => load({ changes: { tab: id, contains: "", ...(id === "pages" ? { page: null } : {}) } }),
      })),
    );
    const crumb = view.page && !isPages
      ? h("div", { class: "crumb" },
          h("span", { text: "کلمات این صفحه:" }),
          h("strong", { class: "ltr", title: view.page, text: readableUrl(view.page) }),
          h("button", {
            class: "btn-link", type: "button", text: "همهٔ صفحه‌ها",
            onclick: () => load({ changes: { page: null } }),
          }))
      : (!isPages
        ? h("div", { class: "crumb muted", text: "برای گرفتن پیشنهاد عنوان، از تب صفحه‌ها یک صفحه را باز کنید و کلمهٔ مورد نظر را تحلیل کنید." })
        : null);
    const head = h("tr", {},
      h("th", { text: isPages ? "صفحه" : "کلمه کلیدی" }),
      sortHeader("clicks", "کلیک‌ها"),
      sortHeader("impressions", "نمایش"),
      sortHeader("ctr", "نرخ کلیک"),
      sortHeader("position", "میانگین جایگاه"),
      isPages ? null : h("th", { title: "جستجوی ماهانه، در صورت موجود بودن", text: "حجم جستجو" }),
      h("th", { title: "کلیک‌ها نسبت به دورهٔ قبل با همین طول", text: "روند" }),
      !isPages && view.page ? h("th", { text: "اقدام" }) : null,
    );
    const body = view.rows.length
      ? view.rows.map(row)
      : h("tr", {}, h("td", { colSpan: 8, class: "empty" },
          view.contains ? "چیزی پیدا نشد." : "برای این بازه داده‌ای نیست. سایت تازه است؟ تا وقتی گوگل داده جمع کند، از ",
          view.contains ? null : h("a", { href: "#/scan", text: "بررسی سایت" }),
          view.contains ? null : " شروع کنید.",
        ));
    tableArea.append(h("section", { class: "data-card" },
      h("div", { class: "data-card-header" }, tabs, search),
      crumb,
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, head), h("tbody", {}, body))),
      view.nextOffset !== null
        ? h("div", { class: "table-foot" }, h("button", {
            class: "btn", type: "button", text: "نمایش بیشتر",
            onclick: () => load({ more: true }),
          }))
        : null,
    ));
    return search;
  }

  // Every way of changing what is shown comes through here. Only the newest
  // request counts, and it carries whatever the ones it overtook were asking for.
  async function load({ changes = {}, more = false, withChart = false, keepFocus = false } = {}) {
    const token = ++loadToken;
    const current = () => alive() && token === loadToken;
    clearTimeout(searchTimer);
    Object.assign(pending, changes);
    chartPending ||= withChart;
    const wanted = { ...view, ...pending };
    const append = more && !chartPending && Object.keys(pending).length === 0;
    drawFilters(wanted);
    loadNotice.className = "hidden";
    try {
      const fetchChart = () => api(`${base}/performance`, {
        query: { ...range(wanted), interval: RANGES.find((item) => item.days === wanted.days).interval || "day", device: wanted.device },
      });
      let chartReport = chartPending ? await fetchChart() : null;
      if (!current()) return;
      if (chartReport && !endDay) {
        // Not knowing the last day, the first request got the server's default
        // range, which may not be the one asked for by now.
        endDay = chartReport.end;
        if (chartReport.days_in_range !== wanted.days) chartReport = await fetchChart();
        if (!current()) return;
      }
      const report = await api(`${base}/performance/${wanted.tab}`, {
        query: {
          ...range(wanted), device: wanted.device, sort: wanted.sort, order: wanted.order, limit: PAGE_SIZE,
          offset: append ? view.nextOffset : 0, compare: "previous", contains: wanted.contains.trim(),
          page: wanted.tab === "queries" ? wanted.page : null,
        },
      });
      if (!current()) return;
      Object.assign(view, pending);
      pending = {};
      chartPending = false;
      if (chartReport) showPerformance(chartReport);
      view.report = report;
      view.rows = append ? [...view.rows, ...report.rows] : report.rows;
      view.nextOffset = report.next_offset;
      const search = drawTable();
      if (keepFocus) {
        search.focus();
        search.setSelectionRange(search.value.length, search.value.length);
      }
    } catch (error) {
      if (!current()) return;
      const retry = { changes: pending, more, withChart: chartPending };
      pending = {};
      chartPending = false;
      drawFilters(view);
      loadNotice.className = "notice notice-error";
      loadNotice.replaceChildren(`${messageFor(error)} `,
        h("button", { class: "btn-link", type: "button", text: "تلاش دوباره", onclick: () => load(retry) }));
    }
  }

  const rangeButtons = h("div", { class: "filters-group", role: "group", "aria-label": "بازهٔ زمانی" });
  const deviceButtons = h("div", { class: "filters-group", role: "group", "aria-label": "دستگاه" });

  function drawFilters(shown) {
    clear(rangeButtons);
    for (const item of RANGES) {
      rangeButtons.append(h("button", {
        class: `filter-btn${shown.days === item.days ? " active" : ""}`, type: "button", text: item.label,
        "aria-pressed": String(shown.days === item.days),
        onclick: () => load({ changes: { days: item.days }, withChart: true }),
      }));
    }
    clear(deviceButtons);
    for (const item of DEVICES) {
      deviceButtons.append(h("button", {
        class: `filter-btn${shown.device === item.value ? " active" : ""}`, type: "button", text: item.label,
        "aria-pressed": String(shown.device === item.value),
        onclick: () => load({ changes: { device: item.value }, withChart: true }),
      }));
    }
  }

  drawFilters(view);
  drawKpis();
  container.append(
    h("div", { class: "page-header" },
      h("div", {},
        h("h1", { class: "page-title", text: "عملکرد سایت" }),
        h("p", { class: "page-sub ltr", text: site.gsc_property_url || "" }),
      ),
      h("div", { class: "filters-group" }, rangeButtons, h("span", { class: "filter-sep" }), deviceButtons),
    ),
    syncNote, kpis,
    h("section", { class: "chart-card" }, chart, chartNote),
    runNotice, loadNotice, tableArea,
  );

  if (sync?.state === "backfilling") {
    syncNote.textContent = `تاریخچهٔ سرچ کنسول هنوز در حال دریافت است (${fmt.int(Math.round(sync.progress * 100))}٪). عددهای بازه‌های قدیمی‌تر کم‌کم کامل می‌شوند.`;
    syncNote.classList.remove("hidden");
  } else if (sync?.state === "failing") {
    syncNote.textContent = "گوگل به درخواست‌های اخیر جواب نداده است؛ به‌طور خودکار دوباره تلاش می‌شود. اگر ادامه داشت، حساب گوگل را در تنظیمات پروژه دوباره وصل کنید.";
    syncNote.classList.remove("hidden");
  }
  await load({ withChart: true });

  // The chart's width is measured, so a resized window redraws it.
  let resizeTimer = null;
  const observer = new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (alive()) drawChart(); else observer.disconnect(); }, 150);
  });
  observer.observe(chart);
}
