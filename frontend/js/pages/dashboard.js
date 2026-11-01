// The command centre: how the agent is doing, what waits for you, what has happened.

import { api } from "../api.js";
import { h, fmt } from "../dom.js";
import { SYNC_STATE, eventLabel, OUTCOME_EVENTS, BAD_EVENTS, QUIET_EVENTS, needsYou, statusLabel } from "../labels.js";
import { loadActions } from "./actions.js";

export function noSiteYet() {
  return h("div", { class: "card-panel empty" },
    h("p", { text: "هنوز سایتی اضافه نکرده‌اید. اول سایتتان را اضافه و سرچ کنسول را وصل کنید." }),
    h("a", { class: "btn btn-primary", href: "#/settings", text: "افزودن سایت" }),
  );
}

function activityBody(item) {
  const detail = item.detail || {};
  if (item.kind === "sync") {
    if (item.event === "search_console_sync") return `دادهٔ ${fmt.int(detail.days_synced)} روز دریافت و ذخیره شد.`;
    return "سرچ کنسول برای بعضی روزها جواب نداد؛ به‌طور خودکار دوباره تلاش می‌شود.";
  }
  if (OUTCOME_EVENTS.has(item.event) && detail.relative_change !== null && detail.relative_change !== undefined) {
    return `«${item.title}»: تغییر نرخ کلیک ${fmt.signedPct(detail.relative_change)}`;
  }
  if (detail.apply_title) return `«${detail.apply_title}»`;
  if (detail.restore_title) return `«${detail.restore_title}»`;
  return item.title ? `«${item.title}»` : null;
}

function timelineItem(item) {
  const body = activityBody(item);
  const isOutcome = OUTCOME_EVENTS.has(item.event);
  const title = h("div", { class: "timeline-title", text: eventLabel(item.event) });
  return h("div", { class: `timeline-item${BAD_EVENTS.has(item.event) ? " bad" : ""}` },
    h("span", { class: "time", text: fmt.moment(item.at) }),
    isOutcome
      ? h("div", { class: `timeline-body${item.event === "outcome_success" ? " feedback-loop" : ""}` }, title, body)
      : [title, body ? h("div", { class: "timeline-body", text: body }) : null],
  );
}

export async function dashboardPage(container, { site, alive }) {
  if (!site) {
    container.append(noSiteYet());
    return;
  }

  const [sync, actions, activity] = await Promise.all([
    api(`/v1/sites/${encodeURIComponent(site.site_id)}/sync`).catch(() => null),
    loadActions(site.site_id),
    api(`/v1/sites/${encodeURIComponent(site.site_id)}/activity`, { query: { limit: 80 } }),
  ]);
  if (!alive()) return;
  const events = activity.filter((item) => !QUIET_EVENTS.has(item.event)).slice(0, 12);

  const syncInfo = SYNC_STATE[sync?.state] || {
    title: "وضعیت: نامشخص", text: "وضعیت همگام‌سازی این سایت در دسترس نیست.", dot: "idle",
  };
  const progress = sync?.state === "backfilling" ? ` ${fmt.int(Math.round(sync.progress * 100))}٪ انجام شده.` : "";
  const statusBar = h("div", { class: "agent-status-bar" },
    h("div", { class: "status-info" },
      h("div", { class: `pulse-dot ${syncInfo.dot}`, "aria-hidden": "true" }),
      h("div", { class: "status-text" },
        h("h2", { text: syncInfo.title }),
        h("p", { text: syncInfo.text + progress }),
      ),
    ),
    sync?.newest_synced_day
      ? h("div", { class: "next-sync", text: `آخرین روز دریافت‌شده: ${fmt.day(sync.newest_synced_day)}` })
      : null,
  );

  const waiting = actions.filter((action) => needsYou(action, site));

  const approvals = h("section", { class: "card-panel" },
    h("div", { class: "panel-title" },
      "کارهای در انتظار شما",
      waiting.length ? h("span", { class: "badge badge-warn", text: `${fmt.int(waiting.length)} مورد` }) : null,
    ),
    waiting.length === 0
      ? h("div", { class: "empty" },
          h("p", { text: "الان کاری منتظر شما نیست." }),
          site.gsc_configured
            ? h("p", { class: "small", text: "برای گرفتن پیشنهاد، در صفحهٔ عملکرد سایت یک صفحه و کلمهٔ کلیدی را تحلیل کنید." })
            : null,
          site.gsc_configured
            ? h("a", { class: "btn", href: "#/gsc", text: "رفتن به عملکرد سایت" })
            : h("a", { class: "btn btn-primary", href: "#/settings", text: "وصل کردن سرچ کنسول" }),
        )
      : waiting.slice(0, 4).map((action) => {
          const label = statusLabel(action.status);
          return h("div", { class: "approval-item" },
            h("div", { class: "approval-header" },
              h("span", { text: action.normalized_query }),
              h("span", { class: "approval-date", text: fmt.moment(action.updated_at) }),
            ),
            h("div", { class: "approval-desc" },
              action.status === "awaiting_approval"
                ? `برای این کلمه عنوان تازه‌ای پیشنهاد شده است: «${action.proposed_title}»`
                : action.status === "approved"
                  ? `عنوان «${action.proposed_title}» را تأیید کرده‌اید؛ منتظر است آن را در سایتتان بگذارید.`
                  : `خواسته‌اید این تغییر برگردد؛ منتظر است عنوان قبلی «${action.applied?.previous_value ?? ""}» را در سایتتان بگذارید.`,
            ),
            h("div", { class: "approval-actions" },
              h("a", { class: "btn btn-primary", href: `#/inbox/${encodeURIComponent(action.action_id)}`, text: action.status === "awaiting_approval" ? "مشاهده و تصمیم" : "ادامه" }),
              action.status === "awaiting_approval" || action.status === "approved"
                ? h("span", { class: "badge badge-warn", text: label.text })
                : h("span", { class: "badge badge-warn", text: "در انتظار برگرداندن" }),
            ),
          );
        }),
    waiting.length > 4 ? h("div", { class: "table-foot" }, h("a", { class: "btn", href: "#/inbox", text: "دیدن همه" })) : null,
  );

  const log = h("section", { class: "card-panel" },
    h("div", { class: "panel-title", text: "گزارش عملیات سیستم" }),
    events.length === 0
      ? h("div", { class: "empty", text: "هنوز فعالیتی ثبت نشده است." })
      : h("div", { class: "timeline" }, events.map(timelineItem)),
  );

  container.append(statusBar, h("div", { class: "dashboard-grid" }, approvals, log));
}
