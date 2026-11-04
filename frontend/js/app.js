// The app: who is signed in, which site is chosen, and which page is showing.

import { api, setCsrf, whenSignedOut, messageFor } from "./api.js";
import { h, clear, fmt, storedGet, storedSet } from "./dom.js";
import { loginPage } from "./pages/login.js";
import { dashboardPage } from "./pages/dashboard.js";
import { inboxPage } from "./pages/inbox.js";
import { gscPage } from "./pages/gsc.js";
import { scanPage } from "./pages/scan.js";
import { settingsPage } from "./pages/settings.js";
import { billingPage } from "./pages/billing.js";
import { loadActions } from "./pages/actions.js";
import { needsYou } from "./labels.js";

const root = document.getElementById("app");
const SITE_KEY = "hoshyarseo.site";

const state = { me: null, sites: [], siteId: null };
let shell = null;
let navigation = 0;

const PAGES = [
  { path: "", title: "مرکز فرماندهی ایجنت", render: dashboardPage },
  { path: "scan", title: "بررسی سایت", render: scanPage },
  { path: "inbox", title: "صندوق تأیید", render: inboxPage, counted: true },
  { path: "gsc", title: "عملکرد سایت (GSC)", render: gscPage },
  { path: "settings", title: "تنظیمات پروژه", render: settingsPage },
  { path: "billing", title: "اشتراک", render: billingPage },
];

function currentRoute() {
  // "#/billing?payment=paid": the query is the page's, not part of its name.
  const [hash, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const [path, ...rest] = hash.split("/");
  let param = query ? `?${query}` : null;
  if (rest.length) {
    try { param = decodeURIComponent(rest.join("/")); } catch { /* a broken link: the page without it */ }
  }
  return { path, param };
}

function currentSite() {
  return state.sites.find((site) => site.site_id === state.siteId) || null;
}

async function loadSites() {
  state.sites = await api("/v1/sites");
  const saved = state.siteId || storedGet(SITE_KEY);
  const known = state.sites.find((site) => site.site_id === saved);
  // The list is oldest first, and the newest is the one being worked on.
  state.siteId = known ? known.site_id : state.sites[state.sites.length - 1]?.site_id || null;
}

function buildShell() {
  const nav = h("nav", { class: "nav", "aria-label": "منوی اصلی" });
  const links = new Map();
  let countBadge = null;
  for (const page of PAGES) {
    const count = page.counted ? h("span", { class: "nav-count hidden" }) : null;
    if (count) countBadge = count;
    const link = h("a", { class: "nav-link", href: `#/${page.path}` }, h("span", { text: page.title }), count);
    links.set(page.path, link);
    nav.append(link);
  }

  const frame = h("div", { class: "shell" });
  const closeMenu = () => frame.classList.remove("menu-open");
  nav.addEventListener("click", closeMenu);

  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" }, h("span", { class: "brand-icon gold-text", "aria-hidden": "true", text: "✦" }), "هوشیار سئو"),
    nav,
    h("button", { class: "signout", type: "button", onclick: signOut, text: "خروج از حساب" }),
  );

  const siteSelect = h("select", {
    class: "site-select", "aria-label": "انتخاب سایت",
    onchange: (event) => {
      state.siteId = event.target.value;
      storedSet(SITE_KEY, state.siteId);
      renderRoute();
    },
  });
  const who = state.me.email || state.me.mobile || "";
  const initial = (state.me.tenant_name || who || "?").trim().charAt(0).toUpperCase();
  const header = h("header", { class: "top-header" },
    h("div", { class: "top-start" },
      h("button", {
        class: "menu-button", type: "button", "aria-label": "باز کردن منو", text: "☰",
        onclick: () => frame.classList.toggle("menu-open"),
      }),
      siteSelect,
    ),
    h("div", { class: "user-menu" },
      h("span", { class: "user-name", title: who, text: state.me.tenant_name || who }),
      h("div", { class: "avatar", "aria-hidden": "true", text: initial }),
    ),
  );
  const content = h("main", { class: "content-area", id: "content" });
  const planNotice = h("div", { class: "hidden" });
  frame.append(
    h("div", { class: "scrim", onclick: closeMenu }),
    sidebar,
    h("div", { class: "main-wrapper" }, header, planNotice, content),
  );
  clear(root).append(frame);
  showPlanNotice(planNotice);
  return { links, countBadge, siteSelect, content };
}

// A trial or a plan near its end says so above every page, with the way to renew.
async function showPlanNotice(holder) {
  let plan;
  try {
    plan = await api("/v1/billing", { quiet: true });
  } catch {
    return; // no plans on this server, or a moment's error: nothing to say
  }
  if (!plan.plan || (plan.active && plan.plan !== "trial" && plan.days_left > 3)) return;
  let text;
  if (!plan.active) text = "اشتراک شما تمام شده است؛ برای انجام کارهای جدید، اشتراک را تمدید کنید.";
  else if (plan.plan === "trial") text = `دورهٔ آزمایشی: ${fmt.int(plan.days_left)} روز مانده است.`;
  else text = `اشتراک شما ${fmt.int(plan.days_left)} روز دیگر تمام می‌شود.`;
  holder.className = `plan-notice${plan.active ? "" : " plan-notice-ended"}`;
  holder.append(h("span", { text }), " ", h("a", { href: "#/billing", text: plan.active && plan.plan === "trial" ? "خرید اشتراک" : "تمدید" }));
}

function syncShell(route) {
  for (const [path, link] of shell.links) {
    const active = path === route.path;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  clear(shell.siteSelect);
  for (const site of state.sites) {
    shell.siteSelect.append(h("option", { value: site.site_id, text: site.name, selected: site.site_id === state.siteId }));
  }
  shell.siteSelect.classList.toggle("hidden", state.sites.length === 0);
}

async function refreshCount() {
  if (!shell || !state.siteId) {
    shell?.countBadge.classList.add("hidden");
    return;
  }
  try {
    const actions = await loadActions(state.siteId);
    const waiting = actions.filter((action) => needsYou(action, currentSite())).length;
    shell.countBadge.textContent = fmt.int(waiting);
    shell.countBadge.classList.toggle("hidden", waiting === 0);
  } catch {
    /* the badge is a convenience; the page itself reports errors */
  }
}

async function renderRoute() {
  const route = currentRoute();
  const token = ++navigation;

  if (!state.me) {
    shell = null;
    clear(root).append(loginPage({ onSignedIn: start }));
    document.title = "ورود به هوشیار سئو";
    return;
  }
  if (route.path === "login") {
    window.location.hash = "#/";
    return;
  }
  const page = PAGES.find((item) => item.path === route.path) || PAGES[0];
  if (!shell) shell = buildShell();
  syncShell({ path: page.path });
  document.title = `${page.title} | هوشیار سئو`;
  clear(shell.content);

  const context = {
    state,
    param: route.param,
    site: currentSite(),
    alive: () => token === navigation,
    rerender: renderRoute,
    refreshCount,
    refreshSites: async (siteId) => {
      await loadSites();
      if (siteId) {
        state.siteId = siteId;
        storedSet(SITE_KEY, siteId);
      }
      syncShell({ path: page.path });
    },
  };
  try {
    await page.render(shell.content, context);
  } catch (error) {
    if (context.alive()) {
      clear(shell.content).append(h("div", { class: "notice notice-error", role: "alert", text: messageFor(error) }));
    }
  }
  refreshCount();
}

async function signOut() {
  try {
    await api("/v1/auth/logout", { method: "POST" });
  } catch (error) {
    // The session is still good on the server: showing the sign-in page now
    // would tell someone at a shared computer they are out when they are not.
    shell?.content.prepend(h("div", { class: "notice notice-error", role: "alert", text: `خروج انجام نشد. ${messageFor(error)}` }));
    return;
  }
  state.me = null;
  setCsrf(null);
  renderRoute();
}

async function start() {
  try {
    state.me = await api("/v1/auth/me", { quiet: true });
    setCsrf(state.me.csrf_token);
    await loadSites();
  } catch (error) {
    if (error.status !== 401) {
      clear(root).append(h("div", { class: "boot", role: "alert", text: messageFor(error) }));
      return;
    }
    state.me = null;
  }
  shell = null;
  renderRoute();
}

whenSignedOut(() => {
  state.me = null;
  setCsrf(null);
  renderRoute();
});
window.addEventListener("hashchange", () => {
  window.scrollTo(0, 0);
  renderRoute();
});
start();
