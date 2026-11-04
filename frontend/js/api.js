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
  ["Scan the site first", "اول سایت را بررسی کنید تا صفحه‌هایش را پیدا کنیم و برای هر کدام کلمه پیشنهاد بدهیم."],
  ["title for this keyword is already being made", "عنوان این کلمه همین الان در حال آماده شدن است."],
  ["title for this keyword was asked for a moment ago", "همین چند دقیقه پیش برای این کلمه عنوان خواسته شد. کمی بعد دوباره امتحان کنید."],
  ["found no page to suggest", "بررسی سایت صفحه‌ای پیدا نکرد که برایش کلمه پیشنهاد کنیم."],
  ["already being made", "پیشنهاد کلمه برای این سایت همین الان در جریان است."],
  ["suggested a moment ago", "همین چند دقیقه پیش کلمه پیشنهاد شد. کمی بعد دوباره امتحان کنید."],
  ["most target keywords", "این سایت به سقف تعداد کلمه‌های هدف رسیده است. اول یکی را حذف کنید."],
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
  if (error.status === 422) return "اطلاعات واردشده معتبر نیست.";
  if (error.status === 429) return "درخواست‌ها زیاد بوده است. کمی بعد دوباره امتحان کنید.";
  if (error.status === 503) return "این بخش روی سرور فعال نیست.";
  return "سرور نتوانست این درخواست را انجام دهد. کمی بعد دوباره امتحان کنید.";
}
