// What each status and event means, in Persian. An unknown value is shown as a
// neutral phrase rather than as an internal word.

export const STATUS = {
  awaiting_approval: { text: "در انتظار تأیید شما", tone: "warn" },
  approved: { text: "تأیید شده", tone: "warn" },
  rejected: { text: "رد شده", tone: "" },
  executing: { text: "در حال اعمال", tone: "" },
  executed: { text: "اعمال شده", tone: "" },
  waiting_for_recrawl: { text: "در انتظار بررسی", tone: "" },
  measurement_window_active: { text: "در حال سنجش نتیجه", tone: "" },
  evaluating: { text: "در حال ارزیابی", tone: "" },
  succeeded: { text: "موفق", tone: "good" },
  failed: { text: "ناموفق", tone: "bad" },
  inconclusive: { text: "نامشخص", tone: "" },
  rolled_back: { text: "برگردانده شده", tone: "" },
};

export function statusLabel(status) {
  return STATUS[status] || { text: "در حال پردازش", tone: "" };
}

export const IN_PROGRESS = new Set(["approved", "executing", "executed", "waiting_for_recrawl", "measurement_window_active", "evaluating"]);

// Waiting on a person: a decision, or on a manual site the title to put in place.
export function needsYou(action, site) {
  if (action.status === "awaiting_approval") return true;
  if (site?.site_adapter_type !== "manual") return false;
  if (action.status === "approved") return true;
  // Undoing on a manual site: the old title is theirs to put back.
  return action.status !== "rolled_back"
    && action.history[action.history.length - 1]?.reason === "waiting_for_customer_to_restore";
}
export const ROLLBACKABLE = new Set(["executed", "waiting_for_recrawl", "measurement_window_active", "evaluating", "failed", "succeeded", "inconclusive"]);

export const EVENTS = {
  title_proposal_generated: "عنوان تازه پیشنهاد شد",
  approved: "پیشنهاد تأیید شد",
  rejected: "پیشنهاد رد شد",
  execution_started: "اعمال تغییر شروع شد",
  execution_succeeded: "عنوان روی سایت اعمال شد",
  already_applied: "عنوان از قبل روی سایت بود",
  applied_by_customer: "عنوان روی سایت دیده شد",
  customer_reported_applied: "اعلام کردید عنوان را اعمال کرده‌اید",
  waiting_for_customer_to_apply: "در انتظار اعمال عنوان توسط شما",
  waiting_for_recrawl: "بررسی صفحه پس از تغییر",
  verification_verified: "تغییر روی صفحه تأیید شد؛ سنجش شروع شد",
  verification_failed: "عنوان نوشته شد ولی روی صفحه دیده نمی‌شود",
  verification_inconclusive: "بررسی صفحه نتیجهٔ روشنی نداد",
  execution_failed: "سایت این تغییر را نپذیرفت",
  execution_error: "خطا هنگام اعمال تغییر",
  adapter_cannot_apply: "این اتصال نمی‌تواند عنوان را تغییر دهد",
  page_changed_since_proposal: "عنوان صفحه بعد از پیشنهاد عوض شده بود؛ چیزی نوشته نشد",
  measurement_ready_for_evaluation: "دادهٔ سنجش آماده شد",
  outcome_success: "نتیجه: موفق",
  outcome_failure: "نتیجه: ناموفق",
  outcome_inconclusive: "نتیجه: نامشخص",
  rollback_requested: "درخواست برگرداندن ثبت شد",
  rolled_back: "تغییر برگردانده شد",
  already_rolled_back: "عنوان قبلی از قبل برگشته بود",
  restored_by_customer: "عنوان قبلی روی سایت دیده شد",
  waiting_for_customer_to_restore: "در انتظار برگرداندن عنوان توسط شما",
  rollback_refused: "برگرداندن انجام نشد",
  rollback_refused_page_changed: "عنوان صفحه بعد از این تغییر عوض شده بود؛ برگردانده نشد",
  rollback_failed: "سایت برگرداندن را نپذیرفت",
  rollback_not_verified: "عنوان قبلی نوشته شد ولی روی صفحه دیده نمی‌شود",
  rollback_error: "خطا هنگام برگرداندن",
  search_console_sync: "دریافت داده از سرچ کنسول",
  search_console_sync_failed: "مشکل در دریافت داده از سرچ کنسول",
};

export function eventLabel(event) {
  return EVENTS[event] || "رویداد سیستم";
}

// Steps inside one change: kept in the action's own history, left out of the
// site-wide log, where they would bury what actually happened.
export const QUIET_EVENTS = new Set([
  "execution_started", "execution_succeeded", "applied_by_customer", "waiting_for_recrawl",
  "measurement_ready_for_evaluation", "customer_reported_applied", "waiting_for_customer_to_apply",
  "waiting_for_customer_to_restore", "rollback_requested",
]);

export const OUTCOME_EVENTS = new Set(["outcome_success", "outcome_failure", "outcome_inconclusive"]);
export const BAD_EVENTS = new Set([
  "outcome_failure", "execution_failed", "execution_error", "verification_failed",
  "rollback_failed", "rollback_error", "search_console_sync_failed",
]);

export const SYNC_STATE = {
  up_to_date: { title: "وضعیت: فعال و به‌روز", text: "دادهٔ سرچ کنسول این سایت کامل است و پیشنهادها روی آن ساخته می‌شوند.", dot: "live" },
  backfilling: { title: "وضعیت: فعال، در حال دریافت تاریخچه", text: "تاریخچهٔ ۱۶ ماههٔ سرچ کنسول روز به روز دریافت می‌شود.", dot: "live" },
  failing: { title: "وضعیت: دریافت داده مشکل دارد", text: "گوگل به درخواست‌های اخیر جواب نداده است. به‌طور خودکار دوباره تلاش می‌شود؛ اگر ادامه داشت، حساب گوگل را دوباره وصل کنید.", dot: "bad" },
  not_connected: { title: "وضعیت: سرچ کنسول وصل نیست", text: "برای شروع، سرچ کنسول این سایت را در تنظیمات پروژه وصل کنید.", dot: "idle" },
  not_eligible: { title: "وضعیت: دادهٔ این سایت خوانده نمی‌شود", text: "property سرچ کنسول این سایت باید متعلق به دامنهٔ خودش باشد. در تنظیمات پروژه حساب گوگل خودتان را وصل کنید و property درست را انتخاب کنید.", dot: "warn" },
};
