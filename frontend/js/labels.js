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

// Between approval and the check on the page. With an error, the attempts may
// be over, so neither the label nor the card may say it is still on its way.
export const APPLYING = new Set(["approved", "executing", "executed", "waiting_for_recrawl"]);

export function actionLabel(action) {
  return action.error && APPLYING.has(action.status)
    ? { text: "با مشکل روبه‌رو شد", tone: "warn" }
    : statusLabel(action.status);
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

// What a site scan found, as a person would say it, and what to do about it.
export const ISSUES = {
  home_unreachable: { title: "صفحهٔ اصلی سایت باز نشد", fix: "اگر سایت برای شما باز می‌شود، شاید فایروال یا تنظیمات سرور جلوی موتورهای جستجو را هم گرفته باشد." },
  robots_blocks_everything: { title: "robots.txt گوگل را از کل سایت دور نگه می‌دارد", fix: "تا این قانون برداشته نشود هیچ صفحه‌ای وارد گوگل نمی‌شود. اگر سایت وردپرسی است، در پیشخوان وردپرس به «تنظیمات ← خواندن» بروید و تیک «از موتورهای جستجو بخواه این سایت را فهرست نکنند» را بردارید؛ در غیر این صورت، فایل اصلاح‌شده همین بالاست." },
  robots_error: { title: "robots.txt با خطای سرور جواب می‌دهد", fix: "گوگل در این حالت معمولاً از خواندن سایت صرف‌نظر می‌کند. باید خطای سرور رفع شود." },
  no_robots: { title: "سایت robots.txt ندارد", fix: "جلوی ایندکس شدن را نمی‌گیرد، ولی بهتر است باشد و آدرس نقشهٔ سایت را هم بگوید." },
  no_sitemap: { title: "سایت نقشه ندارد", fix: "با نقشهٔ سایت، گوگل صفحه‌ها را زودتر و کامل‌تر پیدا می‌کند؛ مخصوصاً برای سایت تازه مهم است." },
  sitemap_not_in_robots: { title: "آدرس نقشهٔ سایت در robots.txt نیست", fix: "اضافه شدنش کمک می‌کند موتورهای جستجو نقشه را خودشان پیدا کنند." },
  not_in_sitemap: { title: "صفحه‌هایی که در نقشهٔ سایت نیستند", fix: "این صفحه‌ها از راه لینک پیدا شدند ولی در نقشه نیامده‌اند." },
  sitemap_lists_blocked_page: { title: "نقشهٔ سایت صفحه‌های مسدود را فهرست کرده", fix: "این صفحه‌ها را robots.txt بسته است؛ یا از نقشه حذف شوند یا باز شوند." },
  sitemap_lists_broken_page: { title: "نقشهٔ سایت صفحه‌های خراب را فهرست کرده", fix: "این آدرس‌ها جواب درست نمی‌دهند و باید از نقشه حذف یا اصلاح شوند." },
  sitemap_lists_redirect: { title: "نقشهٔ سایت آدرس‌های ریدایرکت‌شده دارد", fix: "بهتر است نقشه آدرس نهایی صفحه را داشته باشد." },
  robots_unreadable: { title: "متن robots.txt خوانا نبود", fix: "این فایل وجود دارد، ولی نتوانستیم همهٔ آن را مطمئن بخوانیم؛ مثلاً ناقص رسید، خیلی بزرگ بود یا خطایی بالای آن چاپ شده بود. برای همین تغییری برایش پیشنهاد نمی‌شود." },
  sitemap_unreadable: { title: "نقشهٔ سایت خوانده نشد", fix: "نقشهٔ سایت جواب نداد یا روی دامنهٔ دیگری است، پس دربارهٔ آن چیزی نمی‌گوییم. کمی بعد دوباره بررسی کنید." },
  page_no_answer: { title: "صفحه‌هایی که جواب ندادند", fix: "شاید موقتی باشد. کمی بعد دوباره بررسی کنید." },
  broken_page: { title: "لینک به صفحه‌های خراب", fix: "این آدرس‌ها جواب درست نمی‌دهند. لینک‌ها را اصلاح کنید یا صفحه را برگردانید." },
  noindex_in_sitemap: { title: "صفحهٔ noindex داخل نقشهٔ سایت", fix: "این صفحه‌ها به گوگل می‌گویند ایندکس نشوند، ولی نقشه آن‌ها را معرفی می‌کند. اگر عمدی نیست، noindex را بردارید." },
  noindex: { title: "صفحه‌های noindex", fix: "این صفحه‌ها وارد گوگل نمی‌شوند. اگر عمدی نیست، noindex را بردارید." },
  canonical_elsewhere: { title: "canonical به صفحهٔ دیگری اشاره می‌کند", fix: "گوگل به‌جای این صفحه آن یکی را نشان می‌دهد. اگر عمدی نیست اصلاحش کنید." },
  missing_title: { title: "صفحه‌های بدون عنوان", fix: "عنوان مهم‌ترین نوشتهٔ صفحه در نتایج گوگل است." },
  title_too_short: { title: "عنوان خیلی کوتاه", fix: "عنوان باید بگوید صفحه دربارهٔ چیست و کلمهٔ اصلی را داشته باشد." },
  title_too_long: { title: "عنوان طولانی", fix: "گوگل عنوان‌های خیلی بلند را کوتاه نشان می‌دهد؛ حدود ۶۰ کاراکتر مناسب است." },
  duplicate_title: { title: "عنوان تکراری", fix: "هر صفحه باید عنوان مخصوص خودش را داشته باشد." },
  missing_description: { title: "صفحه‌های بدون توضیحات متا", fix: "توضیحات خوب می‌تواند کلیک از نتایج گوگل را بیشتر کند." },
  description_too_long: { title: "توضیحات متا طولانی", fix: "حدود ۱۵۰ کاراکتر معمولاً کامل نشان داده می‌شود." },
  duplicate_meta_description: { title: "توضیحات متا تکراری", fix: "هر صفحه باید توضیحات مخصوص خودش را داشته باشد." },
  missing_h1: { title: "صفحه‌های بدون تیتر اصلی (H1)", fix: "هر صفحه بهتر است یک تیتر اصلی روشن داشته باشد." },
  several_h1: { title: "چند تیتر اصلی (H1) در یک صفحه", fix: "بهتر است یک H1 اصلی باشد و بقیه تیتر فرعی شوند." },
  thin_content: { title: "صفحه‌های کم‌محتوا", fix: "صفحه با متن خیلی کم معمولاً رتبهٔ خوبی نمی‌گیرد. اگر سایت متنش را با جاوااسکریپت می‌سازد، این عدد ممکن است دقیق نباشد." },
};

export function issueLabel(code) {
  return ISSUES[code] || { title: "مورد قابل بررسی", fix: "" };
}

export const FILE_CHANGES = {
  created: "این فایل روی سایت وجود ندارد و ساخته می‌شود.",
  improved: "صفحه‌هایی که نباید در نقشه باشند از آن بیرون می‌روند و صفحه‌هایی که جا مانده‌اند اضافه می‌شوند. بقیهٔ فهرست همان است که بود.",
  added_sitemap: "آدرس نقشهٔ سایت به آن اضافه می‌شود.",
  removed_block_all: "قانونی که گوگل را از کل سایت دور نگه می‌دارد برداشته می‌شود.",
};

// Why an approved file came to the customer, or what the last look saw.
export const FILE_NOTES = {
  different_content: "در این آدرس فایلی هست، ولی متنش با این نسخه یکی نیست.",
  not_a_sitemap: "در این آدرس چیزی هست، ولی نقشهٔ سایت نیست.",
  http_404: "هنوز فایلی در این آدرس دیده نمی‌شود.",
  http_None: "این آدرس جواب نداد. کمی بعد دوباره امتحان کنید.",
  http_403: "سرور اجازهٔ خواندن این آدرس را نمی‌دهد.",
  http_500: "این آدرس با خطای سرور جواب داد.",
  http_401: "سرور برای این آدرس رمز عبور می‌خواهد؛ گوگل هم نمی‌تواند آن را بخواند.",
  http_410: "هنوز فایلی در این آدرس دیده نمی‌شود.",
  http_502: "این آدرس با خطای سرور جواب داد. کمی بعد دوباره امتحان کنید.",
  http_503: "این آدرس با خطای سرور جواب داد. کمی بعد دوباره امتحان کنید.",
  http_504: "این آدرس با خطای سرور جواب داد. کمی بعد دوباره امتحان کنید.",
};

export const SYNC_STATE = {
  up_to_date: { title: "وضعیت: فعال و به‌روز", text: "دادهٔ سرچ کنسول این سایت کامل است و پیشنهادها روی آن ساخته می‌شوند.", dot: "live" },
  backfilling: { title: "وضعیت: فعال، در حال دریافت تاریخچه", text: "تاریخچهٔ ۱۶ ماههٔ سرچ کنسول روز به روز دریافت می‌شود.", dot: "live" },
  failing: { title: "وضعیت: دریافت داده مشکل دارد", text: "گوگل به درخواست‌های اخیر جواب نداده است. به‌طور خودکار دوباره تلاش می‌شود؛ اگر ادامه داشت، حساب گوگل را دوباره وصل کنید.", dot: "bad" },
  not_connected: { title: "وضعیت: سرچ کنسول وصل نیست", text: "برای شروع، سرچ کنسول این سایت را در تنظیمات پروژه وصل کنید.", dot: "idle" },
  not_eligible: { title: "وضعیت: دادهٔ این سایت خوانده نمی‌شود", text: "property سرچ کنسول این سایت باید متعلق به دامنهٔ خودش باشد. در تنظیمات پروژه حساب گوگل خودتان را وصل کنید و property درست را انتخاب کنید.", dot: "warn" },
};
