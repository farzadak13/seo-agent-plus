// صندوق تأیید: every proposal, what it needs from you, and what became of it.

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

  const refresh = async () => {
    if (!alive()) return;
    let fresh;
    try {
      fresh = await loadActions(site.site_id);
    } catch {
      return;  // a later look, or a reload, will catch up
    }
    if (!alive()) return;
    // Unchanged: leave the page alone, so focus and scrolling stay where they are.
    if (JSON.stringify(fresh) === JSON.stringify(actions)) return;
    actions = fresh;
    draw();
    refreshCount();
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
    for (const action of shown) {
      list.append(actionCard(action, { site, refresh, drafts, focused: action.action_id === param }));
    }
  }
  draw();

  container.append(
    h("div", { class: "page-header" },
      h("div", {},
        h("h1", { class: "page-title", text: "صندوق تأیید" }),
        h("p", { class: "page-sub", text: "هیچ تغییری بدون تأیید شما روی سایت اعمال نمی‌شود." }),
      ),
    ),
    h("section", { class: "card-panel" }, tabs, list),
  );
  if (focused) document.getElementById(`action-${focused.action_id}`)?.scrollIntoView({ block: "center" });
}
