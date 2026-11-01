// One proposed title change, through its whole life: decide, apply, measure, undo.

import { api, messageFor } from "../api.js";
import { h, fmt, readableUrl } from "../dom.js";
import { statusLabel, eventLabel, ROLLBACKABLE } from "../labels.js";

// The change is judged after: settle days + the second window + Search Console's lag.
const DAYS_UNTIL_VERDICT = 7 + 28 + 4;

export async function loadActions(siteId) {
  const actions = [];
  let cursor = null;
  do {
    const response = await api(`/v1/sites/${encodeURIComponent(siteId)}/actions`, { query: { limit: 200, cursor } });
    actions.push(...response.actions);
    cursor = response.next_cursor;
  } while (cursor);
  return actions;
}

function actionPath(action, verb) {
  return `/v1/actions/${encodeURIComponent(action.action_id)}${verb ? `/${verb}` : ""}`;
}

function lastEvent(action, reason) {
  for (let i = action.history.length - 1; i >= 0; i -= 1) {
    if (action.history[i].reason === reason) return action.history[i];
  }
  return null;
}

// "idle": can be asked for. "running": a request is with the job. "stuck": the
// job's last attempt raised, so it may have run out of retries; asking again is
// safe (the write carries an idempotency key) and must stay possible.
function rollbackState(action) {
  for (let i = action.history.length - 1; i >= 0; i -= 1) {
    const { reason } = action.history[i];
    if (reason === "rollback_error") return "stuck";
    if (reason === "rollback_requested" || reason === "waiting_for_customer_to_restore") return "running";
    if (["rollback_refused", "rollback_refused_page_changed", "rollback_failed", "rollback_not_verified"].includes(reason)) return "idle";
  }
  return "idle";
}

function errorText(action) {
  if (!action.error) return null;
  const error = action.error;
  if (error.includes("title changed") || error.includes("was changed after")) return "عنوان صفحه بعد از این کار عوض شده است؛ چیزی روی سایت بازنویسی نشد.";
  if (error.includes("does not show")) return "عنوان نوشته شد ولی صفحه آن را نشان نمی‌دهد.";
  if (error.includes("not known")) return "عنوان قبلی این صفحه ثبت نشده است؛ آن را خودتان در سایت برگردانید.";
  if (error.includes("cannot change titles")) return "این اتصال نمی‌تواند عنوان را تغییر دهد.";
  if (error.includes("refused")) return "سایت این تغییر را نپذیرفت. اتصال سایت را در تنظیمات پروژه بررسی کنید.";
  return "این مرحله با خطا روبه‌رو شد. اگر ادامه داشت با پشتیبانی تماس بگیرید.";
}

function copyButton(text) {
  const button = h("button", {
    class: "btn", type: "button", text: "کپی عنوان",
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "کپی شد";
      } catch {
        button.textContent = "کپی نشد؛ دستی انتخاب کنید";
      }
    },
  });
  return button;
}

function titleRow(label, value, emphasised = false) {
  return h("div", { class: "title-row" },
    h("span", { class: "title-label", text: label }),
    h("span", { class: emphasised ? "title-new" : "", text: value || "—" }),
  );
}

function measurementCells(action) {
  const measured = lastEvent(action, "measurement_ready_for_evaluation")?.detail;
  if (!measured?.baseline || !measured?.current) return null;
  const change = action.history.find((event) => event.reason.startsWith("outcome_"))?.detail?.relative_change;
  const cell = (label, value) => h("div", { class: "measure-cell" },
    h("div", { class: "measure-label", text: label }), h("div", { class: "measure-value", text: value }));
  return h("div", { class: "measure" },
    cell("CTR قبل", fmt.pct(measured.baseline.ctr)),
    cell("CTR بعد", fmt.pct(measured.current.ctr)),
    change === null || change === undefined ? null : cell("تغییر CTR", fmt.signedPct(change)),
    cell("کلیک قبل / بعد", `${fmt.int(measured.baseline.clicks)} / ${fmt.int(measured.current.clicks)}`),
  );
}

function historyList(action) {
  return h("details", { class: "small" },
    h("summary", { class: "muted", text: "تاریخچهٔ این تغییر" }),
    h("div", { class: "timeline" },
      [...action.history].reverse().map((event) =>
        h("div", { class: "timeline-item" },
          h("span", { class: "time", text: fmt.moment(event.at) }),
          h("div", { class: "timeline-title", text: eventLabel(event.reason) }),
        )),
    ),
  );
}

// What a person can do at this stage. `refresh` redraws the list from the server.
export function actionCard(action, { site, refresh, drafts, focused = false }) {
  const manual = site?.site_adapter_type === "manual";
  const label = statusLabel(action.status);
  const problem = h("div", { class: "notice notice-error hidden", role: "alert" });

  const act = async (button, path, body, then) => {
    button.disabled = true;
    problem.classList.add("hidden");
    try {
      await api(path, { method: "POST", body: body ?? {} });
      await (then ? then() : refresh());
    } catch (error) {
      problem.textContent = messageFor(error);
      problem.classList.remove("hidden");
      button.disabled = false;
    }
  };
  // Approving and undoing are carried out by a job, which reads and writes the
  // customer's site; keep looking for a while. The list redraws only on a change.
  const refreshSoon = async () => {
    await refresh();
    for (const wait of [2000, 5000, 10000, 20000, 40000]) setTimeout(refresh, wait);
  };

  const body = [];
  const page = /^https?:\/\//i.test(action.normalized_url)
    ? h("a", {
        class: "page-url", href: action.normalized_url, target: "_blank", rel: "noopener noreferrer",
        title: action.normalized_url, text: readableUrl(action.normalized_url),
      })
    : h("span", { class: "page-url", text: action.normalized_url });

  if (action.status === "awaiting_approval") {
    body.push(awaitingApproval(action, { manual, act, refreshSoon, refresh, drafts }));
  } else {
    const applied = action.applied;
    body.push(h("div", { class: "title-compare" }, action.rolled_back
      ? [
          titleRow("عنوان فعلی", applied.previous_value, true),
          titleRow("برگردانده شد", applied.new_value),
        ]
      : [
          titleRow(applied ? "عنوان قبلی" : "عنوان فعلی", applied?.previous_value ?? action.current_title),
          titleRow(applied ? "عنوان تازه" : "پیشنهاد", applied?.new_value ?? action.proposed_title, Boolean(applied)),
        ]));
  }

  if (action.status === "approved" && manual) {
    const title = action.proposed_title;
    const found = lastEvent(action, "waiting_for_customer_to_apply")?.detail?.found;
    // Only once a check has answered, not while the one just asked for is running.
    const last = action.history[action.history.length - 1]?.reason;
    const reported = action.history.some((event) => event.reason === "customer_reported_applied");
    const checked = reported && last === "waiting_for_customer_to_apply";
    const checking = last === "customer_reported_applied";
    const check = h("button", {
      class: "btn btn-primary", type: "button", text: "اعمال کردم، بررسی کن",
      onclick: () => act(check, actionPath(action, "applied"), undefined, refreshSoon),
    });
    const reject = h("button", {
      class: "btn", type: "button", text: "منصرف شدم",
      onclick: () => act(reject, actionPath(action, "reject"), {}),
    });
    body.push(h("div", { class: "apply-box" },
      "این عنوان را در تنظیمات همین صفحهٔ سایتتان بگذارید:",
      h("div", { class: "apply-title", text: title }),
      found ? h("div", { class: "small muted" }, "الان صفحه این را نشان می‌دهد: ", found) : null,
      checked ? h("div", { class: "small", text: "هنوز عنوان تازه روی صفحهٔ عمومی دیده نمی‌شود. اگر سایت کش دارد، آن را پاک کنید یا چند دقیقه بعد دوباره بررسی کنید." }) : null,
      checking ? h("div", { class: "small", text: "در حال بررسی صفحه…" }) : null,
    ));
    body.push(h("div", { class: "approval-actions" }, check, copyButton(title), reject));
  } else if (["approved", "executing", "executed", "waiting_for_recrawl"].includes(action.status)) {
    body.push(h("div", { class: "notice", text: "در حال اعمال و بررسی روی سایت…" }));
  }

  if (action.status === "measurement_window_active" && action.applied) {
    const verdict = new Date(action.applied.applied_at);
    verdict.setDate(verdict.getDate() + DAYS_UNTIL_VERDICT);
    body.push(h("div", { class: "notice", text: `نتیجهٔ این تغییر حدود ${fmt.localDay(verdict)} آماده می‌شود، چون گوگل باید صفحه را دوباره ببیند و ۲۸ روز داده جمع شود.` }));
  }

  const cells = measurementCells(action);
  if (cells) body.push(cells);

  const error = errorText(action);
  if (error) body.push(h("div", { class: "notice notice-warn", text: error }));

  if (ROLLBACKABLE.has(action.status) && action.applied && !action.rolled_back) {
    const restoring = lastEvent(action, "waiting_for_customer_to_restore");
    const rollback = rollbackState(action);
    if (rollback === "running" && restoring && manual) {
      const title = restoring.detail.restore_title;
      const again = h("button", {
        class: "btn btn-primary", type: "button", text: "برگرداندم، بررسی کن",
        onclick: () => act(again, actionPath(action, "rollback"), {}, refreshSoon),
      });
      body.push(h("div", { class: "apply-box" }, "این عنوان قبلی را در سایتتان برگردانید:", h("div", { class: "apply-title", text: title })));
      body.push(h("div", { class: "approval-actions" }, again, copyButton(title)));
    } else if (rollback === "running") {
      body.push(h("div", { class: "notice", text: "در حال برگرداندن تغییر…" }));
    } else {
      const holder = h("div", { class: "approval-actions" });
      const ask = h("button", {
        class: "btn btn-danger", type: "button",
        text: rollback === "stuck" ? "برگرداندن انجام نشد؛ دوباره تلاش کن" : "برگرداندن این تغییر",
        onclick: () => {
          const yes = h("button", {
            class: "btn btn-danger", type: "button", text: "بله، برگردان",
            onclick: () => act(yes, actionPath(action, "rollback"), {}, refreshSoon),
          });
          const no = h("button", { class: "btn", type: "button", text: "نه", onclick: () => holder.replaceChildren(ask) });
          holder.replaceChildren(h("span", { class: "small", text: "عنوان قبلی برگردد؟" }), yes, no);
        },
      });
      holder.append(ask);
      body.push(holder);
    }
  }

  return h("article", { class: "approval-item", id: `action-${action.action_id}`, "data-focused": focused ? "true" : null },
    h("div", { class: "approval-header" },
      h("span", { text: action.normalized_query }),
      h("span", { class: `badge ${label.tone ? `badge-${label.tone}` : ""}`, text: label.text }),
    ),
    page,
    problem,
    body,
    historyList(action),
  );
}

function awaitingApproval(action, { manual, act, refreshSoon, refresh, drafts }) {
  const group = `title-${action.action_id}`;
  // Kept outside the card: the list is redrawn when another action moves on, and
  // that must never turn the title someone chose back into the proposed one.
  const draft = drafts.get(action.action_id) || { choice: action.proposed_title, custom: "" };
  drafts.set(action.action_id, draft);

  const custom = h("input", {
    class: "input", type: "text", maxLength: 300, placeholder: "عنوان خودتان را بنویسید", "aria-label": "عنوان دلخواه",
    value: draft.custom,
    oninput: () => {
      customRadio.checked = true;
      draft.choice = "__custom";
      draft.custom = custom.value;
    },
  });
  const customRadio = h("input", {
    type: "radio", name: group, value: "__custom", "aria-label": "عنوان دلخواه", checked: draft.choice === "__custom",
    onchange: () => { draft.choice = "__custom"; },
  });
  const option = (value, text) => h("label", { class: "option" },
    h("input", { type: "radio", name: group, value, checked: draft.choice === value, onchange: () => { draft.choice = value; } }),
    h("span", { text }));

  const options = h("div", { class: "options" },
    option(action.proposed_title, action.proposed_title),
    action.alternative_titles.map((title) => option(title, title)),
    h("label", { class: "option" }, customRadio, custom),
  );

  const approve = h("button", {
    class: "btn btn-primary", type: "button", text: manual ? "تأیید این عنوان" : "تأیید و اعمال روی سایت",
    onclick: () => {
      const title = draft.choice === "__custom" ? draft.custom.trim() : draft.choice;
      if (!title) { custom.focus(); return; }
      act(approve, actionPath(action, "approve"), title === action.proposed_title ? {} : { title }, refreshSoon);
    },
  });
  const reject = h("button", {
    class: "btn", type: "button", text: "رد کردن",
    onclick: () => act(reject, actionPath(action, "reject"), {}, refresh),
  });

  return h("div", { class: "approval-desc" },
    h("div", { class: "title-compare" }, titleRow("عنوان فعلی", action.current_title)),
    h("div", { class: "label", text: action.alternative_titles.length ? "یکی را انتخاب کنید:" : "عنوان پیشنهادی:" }),
    options,
    h("div", { class: "approval-actions", style: "margin-top:.6rem" }, approve, reject),
  );
}
