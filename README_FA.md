# Stage 34 — اتصال اولیهٔ GSC بر پایهٔ Stage 33 شما

این بسته بر پایهٔ هشت فایل Stage 33 ارسالی شما و نسخهٔ Stage 32c ساخته شده است. PostgreSQL، RuntimeConfig، RuntimeContainer، WorkerHandle و app factory شما حفظ شده‌اند. SQLite یا ورودی جایگزین به پروژه اضافه نمی‌شود.

## نصب

ZIP را در یک پوشهٔ جدا باز کنید. در پوشهٔ نصب‌کننده:

```powershell
python .\apply_stage34.py --project "D:\Projects\SEOAGENT" --check
python .\apply_stage34.py --project "D:\Projects\SEOAGENT"
cd D:\Projects\SEOAGENT
pytest -q tests\unit\test_stage33_runtime.py tests\unit\test_stage33_restart_wiring.py tests\unit\test_stage34_live_wiring.py
pytest -q -m "not integration"
```

نصب‌کننده همهٔ فایل‌های هدف را پیش از نوشتن بررسی می‌کند. اختلاف نسخه باعث توقف بدون تغییر می‌شود. فایل‌های قبلی در stage34_backup_* ذخیره می‌شوند. تفاوت CRLF/LF و فضای خالی ابتدا و انتهای فایل مانع نصب نیست. تغییر تست قبلیِ «live ممنوع است» عمدی است: آن تست اکنون اتصال سرویس live را بررسی می‌کند.

## موارد اضافه‌شده

- حالت live به GSCClient واقعی با transport استاندارد HTTPS و اعتبارنامهٔ access token متصل است. حالت stub قبلی برای توسعه باقی است.
- هر Run بازهٔ جاری و بازهٔ بلافاصله قبل با طول برابر را می‌گیرد؛ دادهٔ خام هر دو بازه ذخیره می‌شود. نتیجهٔ Pipeline در SEORun ذخیره می‌شود.
- تاریخ ردیف از کلید date خوانده می‌شود. برای دادهٔ قدیمی با دو کلید، رفتار قبلی fetch_date حفظ می‌شود. قرارداد ورودی live دقیقاً page/query/date است.
- دادهٔ query و URL هدف جدا می‌شود؛ baseline ناموجود به‌عنوان MISSING_UNKNOWN ثبت می‌شود، نه دادهٔ کامل یا صفر معلوم.
- ساخت Job و Run با تراکنش مشترک PostgreSQL انجام می‌شود. Repository context اتصال مشترک در تراکنش را پشتیبانی می‌کند. شرط افزایش نسخه نیز پیش از replace کنترل می‌شود.
- worker پس از timeout توقف، thread زنده را نگه می‌دارد و اجازهٔ ساخت thread دوم نمی‌دهد. خطای worker به‌صورت نام نوع exception قابل بررسی است.
- قفل advisory PostgreSQL پیش از شروع worker گرفته می‌شود. در این مدل فقط یک worker فعال برای یک دیتابیس مجاز است. قفل تا پایان thread حفظ می‌شود.
- `/readyz` دسترس‌پذیری Repository و زنده‌بودن worker فعال‌شده را گزارش می‌کند؛ این endpoint اتصال گوگل یا CMS را آزمایش نمی‌کند.
- SiteAdapterFactory مستقل از CMS اضافه شده است. REST عمومی، WordPress و ثبت سازندهٔ سفارشی را پشتیبانی می‌کند. `enabled_capabilities` در config می‌تواند قابلیت‌های اعلام‌شدهٔ سایت را محدود کند.
- `/v1/sites/{site_id}/capabilities` قابلیت‌های پیکربندی‌شده را برمی‌گرداند. این‌ها نتیجهٔ آزمون زندهٔ قابلیت‌های CMS نیستند.
- تنظیمات آداپتر پیش از ذخیره‌سازی اعتبارسنجی می‌شوند. credentialهای تعریف‌شدهٔ آداپتر از secret_refs خوانده می‌شوند. username وردپرس نیز باید از secret_refs تأمین شود. اعتبارسنجی تنظیمات درخواست شبکه به CMS نمی‌فرستد.
- api_key و database_dsn از repr تنظیمات حذف شده‌اند؛ model_dump همچنان می‌تواند آن‌ها را داشته باشد و نباید برای logging استفاده شود.

## اجرای برنامه

schema PostgreSQL پروژه باید از قبل با migration خود پروژه ایجاد شده باشد. فایل migration در ضمیمه‌های ارسالی نبود و این بسته آن را تغییر نمی‌دهد.

```powershell
cd D:\Projects\SEOAGENT
$env:SEO_AGENT_API_KEY = "<your-strong-api-key>"
$env:SEO_AGENT_DATABASE_DSN = "postgresql://<user>:<password>@localhost/<database>"
$env:SEO_AGENT_GSC_MODE = "live"
$env:SEO_AGENT_WORKER_ENABLED = "true"
$env:MY_SITE_GSC_TOKEN = "<valid-google-access-token>"
uvicorn app.api.main:app_factory --factory --host 127.0.0.1 --port 8000
```

نام متغیرهای Stage 33 شما حفظ شده است. `app.api.main:app --factory` هم مطابق alias موجود کار می‌کند. تنظیمات اختیاری جدید:

```powershell
$env:SEO_AGENT_GSC_TIMEOUT_SECONDS = "30"
$env:SEO_AGENT_MAX_WINDOW_DAYS = "90"
```

حداکثر بازه مربوط به بازهٔ جاری است؛ baseline نیز همین تعداد روز دارد. access token را در محیط همان فرایند اجرا قرار دهید و آن را در چت یا فایل کد ننویسید. رابط API در `/docs` در دسترس است.

ثبت اتصال GSC برای سایت از API قبلی انجام می‌شود:

```json
{
  "property_url": "https://example.com/",
  "credential_ref": "MY_SITE_GSC_TOKEN",
  "auth_mode": "access_token",
  "row_limit": 25000
}
```

مسیر: `PUT /v1/sites/{site_id}/connections/gsc` با Bearer key خود برنامه. سپس همان endpoint قبلی ایجاد Run را فراخوانی کنید و نتیجه را با `/v1/runs/{run_id}` بخوانید.

## CMSهای مختلف

نمونهٔ تنظیم REST عمومی برای یک سایت با API سازگار:

```json
{
  "adapter_type": "generic_rest",
  "config": {
    "base_url": "https://example.com/api/",
    "read_page": {"path": "pages?url={url}", "method": "GET"},
    "update_title": {"path": "pages/title", "method": "PATCH"},
    "enabled_capabilities": ["read_page", "update_title"]
  },
  "secret_refs": {"authorization_token": "MY_CMS_TOKEN"}
}
```

مسیر: `PUT /v1/sites/{site_id}/connections/site-adapter`. مسیر endpointها و قالب پاسخ باید با آداپتر REST موجود تطبیق داده شوند. CMS با قرارداد متفاوت، سازنده/آداپتر مخصوص نیاز دارد؛ اضافه‌کردن نام CMS به‌تنهایی اتصال آماده ایجاد نمی‌کند.

سازندهٔ سفارشی از `container.adapters.register(adapter_type, config_model, builder, secret_fields=...)` ثبت می‌شود. builder باید `adapter_id` و `config` بپذیرد و SiteAdapter سازگار برگرداند. برای ساخت آداپتر سایت از `container.adapters.build(site)` استفاده کنید.

این مرحله اجرای خودکار اقدام را به Run اضافه نمی‌کند. تأیید انسانی، نگهداری سابقهٔ اجرای CMS و rollback پایدار همچنان در مراحل اقدام/اجرا تکمیل می‌شوند.

## تست و محدودیت اعتبارسنجی

- Stage 33 ارسالی پیش از تغییر: ۱۱ تست پاس شد.
- Stage 33 و Stage 34 پس از ادغام: ۲۵ تست پاس شد؛ ۱۴ مورد مربوط به فایل جدید هستند.
- کل مجموعه در محیط بررسی: `753 passed, 2 deselected, 3 warnings in 14.19s`.
- یک مورد integration کنار گذاشته شد؛ مورد دیگر تست فایل migration است که فایل SQL آن در ضمیمه‌ها وجود ندارد. در پروژهٔ کامل خودتان دستور عادی بدون deselect را اجرا کنید.
- تست مسیر کامل از API به Job، handler، LiveGSCGateway، GSCClient، Pipeline و نتیجهٔ ذخیره‌شده اجرا شده است. پاسخ HTTP و Repository در این تست شبیه‌سازی شده‌اند.
- تست موجود test_stage33_restart_wiring فقط annotation نوع Repository را بررسی می‌کند؛ اثبات restart واقعی PostgreSQL نیست. اتصال واقعی Google و PostgreSQL، بازیابی پس از restart سرور و شاخهٔ واقعی قفل PostgreSQL در این محیط آزموده نشده‌اند.

## کار باقی‌ماندهٔ اتصال GSC

این نسخه اتصال live با access token دارد؛ تمدید خودکار OAuth و service account هنوز پیاده نشده‌اند. مدل property_url فعلی همچنان URL-prefix را می‌پذیرد؛ پشتیبانی sc-domain نیازمند تغییر مدل و تست جداست. query-level URL totals از ردیف‌های query استخراج می‌شوند و جمع مستقل URL-level از گوگل هنوز دریافت نمی‌شود. GSCClient فعلی dataState=all را می‌فرستد؛ سیاست دادهٔ نهایی/تازه باید در مرحلهٔ کیفیت داده تعیین شود. صفحه‌بندی موجود به معنی تضمین دریافت تمام queryهای Search Console نیست.

eventها همچنان حافظه‌ای‌اند و export پایدار موضوع مرحلهٔ Observability عملیاتی است. worker داخل process برنامه اجرا می‌شود؛ از چند worker فعال یا reload برای محیط عملیاتی استفاده نکنید. worker_enabled=false امکان راه‌اندازی API بدون worker را فراهم می‌کند.

مرجع قرارداد تاریخ و ترتیب کلیدها: https://developers.google.com/webmaster-tools/v1/searchanalytics/query
