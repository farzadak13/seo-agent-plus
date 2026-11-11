// The API, on the same origin. The session is an HttpOnly cookie the page never
// sees; what the page holds is the CSRF token, which every change must carry.

let csrfToken = null;
let onSignedOut = () => {};

export function setCsrf(token) { csrfToken = token; }
export function whenSignedOut(callback) { onSignedOut = callback; }

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.message || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, query, quiet = false } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [name, value] of Object.entries(query || {})) {
    if (value !== null && value !== undefined && value !== "") url.searchParams.set(name, value);
  }
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response;
  try {
    response = await fetch(url, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* an empty or non-JSON error */ }
  if (!response.ok) {
    if (response.status === 401 && !quiet) onSignedOut();
    throw new ApiError(response.status, data?.detail ?? null);
  }
  return data;
}

// The API speaks English to developers; the customer reads Persian. Anything
// not listed falls back to a plain sentence for its status, never to raw text.
const KNOWN = [
  ["Too many sites scanned", "امروز سایت‌های زیادی بررسی شده است. فردا دوباره امتحان کنید."],
  ["Too many descriptions asked for today", "امروز به سقف پیشنهاد توضیحات رسیده‌اید. فردا دوباره امتحان کنید."],
  ["Too many codes sent to this number", "امروز کدهای زیادی به این شماره فرستاده شده است. فردا دوباره امتحان کنید."],
  ["Too many codes requested", "درخواست کد زیاد بوده است. کمی بعد دوباره امتحان کنید."],
  ["Too many tries", "تلاش‌ها زیاد بوده است. کمی بعد دوباره امتحان کنید."],
  ["A code was sent a moment ago", "کد همین الان فرستاده شد. کمی صبر کنید و بعد دوباره درخواست کنید."],
  ["not an Iranian mobile number", "شمارهٔ موبایل درست نیست. مثلاً ۰۹۱۲۳۴۵۶۷۸۹ را بنویسید."],
  ["code is not correct or has expired", "کد درست نیست یا وقتش تمام شده است. دوباره امتحان کنید یا کد جدید بگیرید."],
  ["code could not be sent", "پیامک فرستاده نشد. چند دقیقهٔ دیگر دوباره امتحان کنید."],
  ["Sign-up is not open", "ثبت‌نام هنوز باز نشده است."],
  ["Sign-in by mobile is not configured", "ورود با موبایل روی این سرور فعال نیست."],
  ["This account cannot sign in", "این حساب نمی‌تواند وارد شود. با پشتیبانی تماس بگیرید."],
  ["subscription has ended", "اشتراک شما تمام شده است. برای انجام کارهای جدید، از صفحهٔ «اشتراک» تمدید کنید."],
  ["no room for another site", "پلن فعلی‌تان اجازهٔ افزودن سایت دیگری نمی‌دهد. از صفحهٔ «اشتراک» پلن بزرگ‌تری بگیرید."],
  ["no plan to pay for", "این حساب محدودیت پلن ندارد و نیازی به خرید نیست."],
  ["Online payment is not available", "پرداخت آنلاین هنوز فعال نشده است."],
  ["payment gateway did not answer", "درگاه پرداخت جواب نداد. چند دقیقهٔ دیگر دوباره امتحان کنید."],
  ["Too many suggestions asked for today", "امروز به سقف پیشنهاد کلمه رسیده‌اید. فردا دوباره امتحان کنید."],
  ["Too many titles asked for today", "امروز به سقف پیشنهاد عنوان رسیده‌اید. فردا دوباره امتحان کنید."],
  ["email or password is not correct", "ایمیل یا رمز عبور درست نیست."],
  ["Too many", "تلاش‌های ورود زیاد بوده است. چند دقیقه دیگر دوباره امتحان کنید."],
  ["suspended", "این حساب معلق شده است. با پشتیبانی هوشیار سئو تماس بگیرید."],
  ["Connect your Google account first", "اول حساب گوگل خود را وصل کنید."],
  ["property must be this site's own domain", "این property متعلق به دامنهٔ همین سایت نیست."],
  ["cannot see that property", "حساب گوگل شما به این property دسترسی ندارد."],
  ["not readable", "این property در سرچ کنسول هنوز تأیید نشده است."],
  ["Connect the site", "اول اتصال سایت را در تنظیمات پروژه کامل کنید."],
  ["Prove this site is yours", "این اتصال باید با حساب گوگل خودتان باشد. در تنظیمات پروژه حساب گوگل را وصل کنید."],
  ["cannot be analysed", "این سایت الان قابل تحلیل نیست؛ property آن باید متعلق به دامنهٔ خودش باشد."],
  ["Google sign-in is not configured", "ورود با گوگل روی این سرور فعال نیست."],
  ["Sign in with Google is not configured", "ورود با گوگل روی این سرور فعال نیست."],
  ["Credential storage is not configured", "ذخیرهٔ امن رمزها روی این سرور فعال نیست."],
  ["Invalid adapter configuration", "اطلاعات اتصال سایت معتبر نیست."],
  ["changed while", "این مورد همین الان تغییر کرده است. صفحه را تازه کنید و دوباره امتحان کنید."],
  ["already been rolled back", "این تغییر قبلاً برگردانده شده است."],
  ["cannot be rolled back", "این تغییر قابل برگرداندن نیست."],
  ["cannot be approved", "این مورد دیگر در انتظار تأیید نیست."],
  ["cannot be rejected", "این مورد دیگر قابل رد کردن نیست."],
  ["not waiting to be applied", "این مورد در انتظار اعمال نیست."],
  ["already running", "یکی از سایت‌های شما همین الان در حال بررسی است. چند دقیقه بعد دوباره امتحان کنید."],
  ["scanned a moment ago", "این سایت همین چند دقیقه پیش بررسی شد. کمی بعد دوباره امتحان کنید."],
  ["usual web ports", "فقط سایت‌هایی که روی پورت معمول وب هستند بررسی می‌شوند."],
  ["checked a moment ago", "همین چند لحظه پیش بررسی شد. کمی بعد دوباره امتحان کنید."],
  ["not waiting to be checked", "این مورد در انتظار بررسی نیست."],
  ["still under way","یک تغییر روی این سایت هنوز در جریان است. صبر کنید تمام شود یا آن را برگردانید."],
  ["description for this page is already waiting", "برای این صفحه یک پیشنهاد توضیحات در صندوق تأیید منتظر است."],
  ["can change meta descriptions", "افزونهٔ هوشیار سئو روی سایتتان قدیمی است و نمی‌تواند توضیحات متا را تغییر دهد. نسخهٔ تازه را از تنظیمات پروژه بگیرید و روی سایت به‌روز کنید."],
  ["connection did not answer; check it in the project settings", "اتصال سایت جواب نداد. اتصال را در تنظیمات پروژه بررسی کنید و دوباره امتحان کنید."],
  ["Meta descriptions are not configured", "پیشنهاد توضیحات روی این سرور فعال نیست."],
  ["Scan the site first", "اول سایت را بررسی کنید تا صفحه‌هایش را پیدا کنیم و برای هر کدام کلمه پیشنهاد بدهیم."],
  ["title for this keyword is already being made", "عنوان این کلمه همین الان در حال آماده شدن است."],
  ["title for this keyword was asked for a moment ago", "همین چند دقیقه پیش برای این کلمه عنوان خواسته شد. کمی بعد دوباره امتحان کنید."],
  ["found no page to suggest", "بررسی سایت صفحه‌ای پیدا نکرد که برایش کلمه پیشنهاد کنیم."],
  ["already being made", "پیشنهاد کلمه برای این سایت همین الان در جریان است."],
  ["suggested a moment ago", "همین چند دقیقه پیش کلمه پیشنهاد شد. کمی بعد دوباره امتحان کنید."],
  ["Competitors are part of the gold plan", "رتبهٔ رقیب‌ها در اشتراک طلایی است. صفحهٔ «اشتراک» را ببینید."],
  ["not a site address", "آدرس سایت رقیب درست نیست. مثلاً بنویسید: digikala.com"],
  ["is this site itself", "این آدرس خود سایت شماست؛ آدرس یک رقیب را بنویسید."],
  ["most target keywords", "این سایت به سقف کلمه‌های هدف اشتراکتان رسیده است. اول یکی را حذف کنید، یا اشتراک طلایی را ببینید."],
  ["changes for this month are used up", "سهمیهٔ تغییرهای این ماه اشتراکتان تمام شده است. در صفحهٔ «اشتراک» ببینید کِی دوباره آزاد می‌شود."],
  ["not one the scan read", "این صفحه در بررسی سایت دیده نشد. صفحهٔ دیگری انتخاب کنید یا سایت را دوباره بررسی کنید."],
  ["Keep the keyword first", "اول این کلمه را نگه دارید، بعد عنوان بخواهید."],
  ["keyword is empty", "کلمه را بنویسید."],
];

export function messageFor(error) {
  if (!(error instanceof ApiError)) return "مشکلی پیش آمد. دوباره امتحان کنید.";
  if (error.status === 0) return "به سرور وصل نشدیم. اتصال اینترنت را بررسی کنید و دوباره امتحان کنید.";
  const text = String(error.message || "");
  for (const [needle, persian] of KNOWN) {
    if (text.includes(needle)) return persian;
  }
  if (error.status === 401) return "لطفاً دوباره وارد شوید.";
  if (error.status === 403) return "اجازهٔ این کار را ندارید. صفحه را تازه کنید و دوباره امتحان کنید.";
  if (error.status === 404) return "این مورد پیدا نشد.";
  if (error.status === 409) return "این کار الان ممکن نیست. وضعیت را بررسی کنید و دوباره امتحان کنید.";
  if (error.status === 402) return "اشتراک شما اجازهٔ این کار را نمی‌دهد. صفحهٔ «اشتراک» را ببینید.";
  if (error.status === 422) return "اطلاعات واردشده معتبر نیست.";
  if (error.status === 429) return "درخواست‌ها زیاد بوده است. کمی بعد دوباره امتحان کنید.";
  if (error.status === 503) return "این بخش روی سرور فعال نیست.";
  return "سرور نتوانست این درخواست را انجام دهد. کمی بعد دوباره امتحان کنید.";
}
