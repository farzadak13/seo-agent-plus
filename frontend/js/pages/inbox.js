// صندوق تأیید: every proposal, what it needs from you, and what became of it.

import { api, messageFor } from "../api.js";
import { h, clear, fmt } from "../dom.js";
import { IN_PROGRESS, needsYou } from "../labels.js";
import { actionCard, loadActions } from "./actions.js";
import { noSiteYet } from "./dashboard.js";

const EMPTY = {
  waiting: "چیزی منتظر شما نیست. از صفحهٔ عملکرد سایت می‌توانید یک صفحه و کلمه را تحلیل کنید.",
  progress: "تغییری در جریان نیست.",
  done: "هنوز تغییری به پایان نرسیده است.",
};

export async function inboxPage(container, { site, param, alive, refreshCount }) {
  if (!site) {
    container.append(noSiteYet());
    return;
  }

  const tabsSpec = [
    { id: "waiting", title: "در انتظار شما", test: (action) => needsYou(action, site) },
    { id: "progress", title: "در جریان", test: (action) => !needsYou(action, site) && IN_PROGRESS.has(action.status) },
    { id: "done", title: "پایان‌یافته", test: (action) => !needsYou(action, site) && !IN_PROGRESS.has(action.status) },
  ];

  let actions = await loadActions(site.site_id);
  if (!alive()) return;

  const focused = param ? actions.find((action) => action.action_id === param) : null;
  let active = focused ? tabsSpec.find((tab) => tab.test(focused)).id : "waiting";
  if (!focused && !actions.some(tabsSpec[0].test) && actions.some(tabsSpec[1].test)) active = "progress";

  const tabs = h("div", { class: "tabs", role: "tablist" });
  const list = h("div", {});

  const drafts = new Map();  // action id -> the title chosen or typed so far
  // Proposals ticked for deciding together; kept across redraws like drafts.
  const selected = new Set();
  const notice = h("div", { class: "hidden", role: "status" });

  // What approving a proposal sends, as its own card's button would: its text,
  // the other text chosen on the card, or nothing at all (null) when "your own"
  // was chosen and left empty, where the card's button sends nothing either.
  const approvalBody = (action) => {
    const draft = drafts.get(action.action_id);
    if (!draft) return {};
    const text = draft.choice === "__custom" ? draft.custom.trim() : draft.choice;
    if (!text) return null;
    return text !== action.proposed_title ? { title: text } : {};
  };

  const manual = site?.site_adapter_type === "manual";
  let busy = false;
  let bar = null;  // the bar's live parts, updated in place as boxes are ticked

  // One at a time, so the site is not sent a burst; each one's fate is counted.
  const decideAll = async (verb) => {
    const chosen = actions.filter((action) => selected.has(action.action_id) && action.status === "awaiting_approval");
    if (!chosen.length || busy) return;
    busy = true;
    syncBar();
    let done = 0;
    const failed = [];
    for (const action of chosen) {
      if (!alive()) return;
      const body = verb === "approve" ? approvalBody(action) : {};
      if (body === null) {
        failed.push("متن دلخواه یکی از پیشنهادها خالی است.");
        continue;
      }
      try {
        await api(`/v1/actions/${encodeURIComponent(action.action_id)}/${verb}`, { method: "POST", body });
        done += 1;
        selected.delete(action.action_id);
      } catch (error) {
        failed.push(messageFor(error));
        // Signed out, the plan does not allow it, or its changes are used up: the rest would fail the same way.
        if (error.status === 401 || error.status === 402 || error.status === 429) break;
      }
    }
    busy = false;
    if (!alive()) return;
    const what = verb === "approve" ? "تأیید شد" : "رد شد";
    notice.className = failed.length ? "notice notice-warn" : "notice notice-good";
    if (!failed.length) notice.textContent = `${fmt.int(done)} پیشنهاد ${what}.`;
    else if (!done) notice.textContent = `هیچ‌کدام ثبت نشد: ${failed[0]}`;
    else notice.textContent = `${fmt.int(done)} پیشنهاد ${what}؛ ${fmt.int(failed.length)} مورد ثبت نشد: ${failed[0]}`;
    await refresh();
    if (!alive()) return;
    draw();
    // The approved ones are being applied by a job: look again as a single approval does.
    for (const seconds of [3, 10, 30]) setTimeout(() => { refresh(); }, seconds * 1000);
  };

  // The bar follows the ticks without redrawing the list, so focus stays on the box.
  function syncBar() {
    if (!bar) return;
    const count = selected.size;
    bar.approve.textContent = `${manual ? "تأیید" : "تأیید و اعمال"} انتخاب‌شده‌ها (${fmt.int(count)})`;
    bar.approve.disabled = busy || !count;
    bar.reject.disabled = busy || !count;
    bar.all.checked = count === bar.total;
    bar.all.indeterminate = count > 0 && count < bar.total;
    bar.ticks.forEach((tick) => { tick.disabled = busy; });
  }

  function bulkBar(waiting) {
    const decidable = waiting.filter((action) => action.status === "awaiting_approval");
    bar = null;
    if (decidable.length < 2) return null;
    for (const id of [...selected]) {
      if (!decidable.some((action) => action.action_id === id)) selected.delete(id);
    }
    const all = h("input", {
      type: "checkbox", "aria-label": "انتخاب همهٔ پیشنهادها",
      onchange: () => {
        if (all.checked) decidable.forEach((action) => selected.add(action.action_id));
        else selected.clear();
        bar.ticks.forEach((tick) => { tick.checked = selected.has(tick.value); });
        syncBar();
      },
    });
    const approve = h("button", { class: "btn btn-primary", type: "button", onclick: () => decideAll("approve") });
    const reject = h("button", { class: "btn", type: "button", text: "رد انتخاب‌شده‌ها", onclick: () => decideAll("reject") });
    bar = { all, approve, reject, total: decidable.length, ticks: [] };
    return h("div", { class: "bulk-bar" },
      h("label", { class: "check-line" }, all, " همه"), approve, reject,
      h("span", { class: "small muted", text: "هر پیشنهاد با همان متنی تأیید می‌شود که روی کارتش انتخاب شده است." }),
    );
  }

  function tickFor(action) {
    const tick = h("input", {
      type: "checkbox", class: "bulk-tick", value: action.action_id, checked: selected.has(action.action_id),
      "aria-label": `انتخاب پیشنهاد ${action.normalized_query || action.normalized_url}`,
      onchange: () => {
        if (tick.checked) selected.add(action.action_id);
        else selected.delete(action.action_id);
        syncBar();
      },
    });
    bar.ticks.push(tick);
    return tick;
  }

  // False when the list could not be fetched, for whoever is waiting on it.
  const refresh = async () => {
    if (!alive()) return true;
    let fresh;
    try {
      fresh = await loadActions(site.site_id);
    } catch {
      return false;
    }
    if (!alive()) return true;
    // Unchanged: leave the page alone, so focus and scrolling stay where they are.
    if (JSON.stringify(fresh) === JSON.stringify(actions)) return true;
    actions = fresh;
    draw();
    refreshCount();
    return true;
  };

  function draw() {
    clear(tabs);
    for (const tab of tabsSpec) {
      const count = actions.filter(tab.test).length;
      tabs.append(h("button", {
        class: `tab${tab.id === active ? " active" : ""}`, type: "button", role: "tab",
        "aria-selected": String(tab.id === active),
        text: `${tab.title} (${fmt.int(count)})`,
        onclick: () => { active = tab.id; draw(); },
      }));
    }
    clear(list);
    const shown = actions.filter(tabsSpec.find((tab) => tab.id === active).test);
    if (shown.length === 0) {
      list.append(h("div", { class: "empty", text: EMPTY[active] }));
    }
    const barNode = active === "waiting" ? bulkBar(shown) : (bar = null);
    if (barNode) list.append(barNode);
    for (const action of shown) {
      const card = actionCard(action, { site, refresh, drafts, focused: action.action_id === param });
      if (barNode && action.status === "awaiting_approval") {
        list.append(h("div", { class: "bulk-row" }, tickFor(action), card));
      } else {
        list.append(card);
      }
    }
    syncBar();
  }
  draw();

  container.append(
    h("div", { class: "page-header" },
      h("div", {},
        h("h1", { class: "page-title", text: "صندوق تأیید" }),
        h("p", { class: "page-sub", text: "هیچ تغییری بدون تأیید شما روی سایت اعمال نمی‌شود." }),
      ),
    ),
    h("section", { class: "card-panel" }, tabs, notice, list),
  );
  if (focused) document.getElementById(`action-${focused.action_id}`)?.scrollIntoView({ block: "center" });
}
