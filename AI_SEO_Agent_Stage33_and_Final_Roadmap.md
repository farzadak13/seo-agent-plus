# AI SEO Agent — Stage 33 Specification & Final Roadmap

## وضعیت مبنا

این سند بر اساس آخرین snapshot کد و تست ارائه‌شده برای پروژه تهیه شده است.

Baseline فعلی:
- Stage 32c: تکمیل و تست‌شده
- تست Stage 32c: `11 passed`
- کل تست‌های غیر integration: `729 passed, 1 deselected`
- `InMemoryRepository` و `PostgresRepository` در لایه persistence وجود دارند.
- `JobStore` و `RunStore` روی قرارداد عمومی `Repository` کار می‌کنند.
- `JobScheduler` در حال حاضر single-worker و polling-based است.
- `SEORunService` وابستگی‌هایش را از بیرون دریافت می‌کند.
- `GSCClient` و قراردادهای GSC/Baseline/Pipeline وجود دارند.
- Observability از Job تا Run/Pipeline/LLM/Execution متصل شده است.

---

# بخش اول — Stage 33: اتصال ورودی واقعی برنامه

## هدف مرحله

تبدیل composition root فعلی از یک نمونه توسعه‌ای مبتنی بر حافظه و handler آزمایشی به application قابل اجرای واقعی که مسیر زیر را به‌صورت واقعی اجرا کند:

`API → Persistent Repository → Job → Worker → SEO Run Handler → SEORunService → injected dependencies → Decision Pipeline`

### معیار پایان

یک درخواست API برای اجرای SEO Run واقعاً وارد queue شود، توسط worker اجرا شود، وضعیت Job/Run در persistence پایدار ثبت شود و بعد از restart قابل بازیابی باشد.

## وضعیت فعلی

در `app/api/main.py` هنوز `InMemoryRepository()` استفاده می‌شود؛ بنابراین داده‌ها بعد از restart باقی نمی‌مانند.

در registry فقط `echo` ثبت شده و `seo_run` در composition root ثبت نشده است.

Scheduler ساخته شده، اما worker lifecycle واقعی در composition root راه‌اندازی نشده است.

API key نیز هنوز fallback به `change-me` دارد و باید اجباری شود.

---

## محدوده تغییرات Stage 33

### 1. تنظیمات مرکزی Application

ایجاد configuration مرکزی و typed برای runtime.

حداقل تنظیمات:

- `SEO_AGENT_ENV`
- `SEO_AGENT_API_KEY`
- `SEO_AGENT_DATABASE_DSN`
- `SEO_AGENT_WORKER_ENABLED`
- `SEO_AGENT_WORKER_POLL_INTERVAL_SECONDS`
- تنظیمات runtime مربوط به Observability

اصول:

- secret پیش‌فرض production وجود نداشته باشد.
- environment variable فقط در configuration/composition خوانده شود.
- domain/application services به `os.environ` مستقیم وابسته نباشند.
- configuration قابل validate و test باشد.

### 2. Persistent Repository واقعی

استفاده از `PostgresRepository` به‌عنوان repository اصلی application.

کارها:

- دریافت DSN از configuration
- ساخت repository در composition root
- استفاده مشترک آن توسط `JobStore`، `SiteStore` و `RunStore`
- اجرای migrationهای موجود پیش از استفاده
- fail-fast در نبود database configuration یا اتصال ضروری

Repository فعلی PostgreSQL append-only/versioned است و contract عمومی را پیاده می‌کند؛ بنابراین Stage 33 قرار نیست persistence abstraction جدیدی بسازد.

### 3. Composition Root واقعی

`app/api/main.py` نباید business logic داشته باشد، اما باید dependency graph کامل را compose کند:

- Repository
- JobStore
- SiteStore
- RunStore
- SecretResolver
- GSC gateway implementation فعلی/موقت برای Stage 33
- BaselineProvider implementation فعلی/موقت برای Stage 33
- PipelineRunner
- `SEORunService`
- SEO Run Handler
- `JobHandlerRegistry`
- Observability
- `JobScheduler`
- FastAPI application

اصل:

`Composition Root می‌سازد → Service هماهنگ می‌کند → Domain تصمیم می‌گیرد`

### 4. ثبت واقعی SEO Run Handler

ثبت:

`SEO_RUN_JOB_TYPE → build_seo_run_handler(...)`

با dependencyهای واقعی runtime.

Fakeهای تست فقط در تست‌ها باقی می‌مانند.

### 5. Worker واقعی

در Stage 33 مدل فعلی را single-worker نگه می‌داریم.

Worker باید:

- startup شود.
- `recover()` را اجرا کند.
- queue را با `run_forever()` مصرف کند.
- stop/shutdown کنترل‌شده داشته باشد.
- در صورت خاموش‌شدن application بتواند loop را متوقف کند.

برای MVP می‌توان worker را در process برنامه به شکل background thread اجرا کرد، به شرطی که shutdown و error handling به‌درستی تست شوند و architecture امکان جدا کردن worker process در آینده را حفظ کند.

### 6. حذف `change-me`

این fallback باید حذف شود:

`os.getenv("SEO_AGENT_API_KEY", "change-me")`

رفتار جدید:

- نبود متغیر → startup failure
- مقدار خالی → startup failure
- مقدار معتبر → authenticator ساخته شود

کلید خام همچنان نباید در persistence ذخیره شود.

### 7. Startup / Shutdown Lifecycle

Startup:

1. Load configuration
2. Validate required settings
3. Initialize repository
4. Initialize stores
5. Build services
6. Register handlers
7. Initialize observability
8. Start worker if enabled

Shutdown:

1. Signal worker stop
2. اجازه پایان کنترل‌شده عملیات جاری
3. آزادسازی منابع

### 8. API → Worker → Run واقعی

Integration test اصلی Stage 33 باید ثابت کند:

1. ثبت Site با API
2. تنظیم GSC reference
3. ساخت SEO Run
4. ایجاد Job
5. ایجاد Run
6. consume شدن Job توسط worker
7. resolve شدن handler واقعی
8. اجرای `SEORunService`
9. رسیدن Run به status صحیح
10. ثبت result در Job
11. باقی‌ماندن داده‌ها در persistence

در این مرحله GSC واقعی هنوز لازم نیست؛ dependency بیرونی می‌تواند fake/controlled adapter باشد. هدف Stage 33 اثبات wiring، persistence و runtime execution است.

### 9. Restart Persistence Test

سناریوی اجباری:

- Application instance اول داده ایجاد کند.
- Instance اول متوقف شود.
- Instance دوم با همان PostgreSQL بالا بیاید.
- Site/Job/Run قبلی قابل خواندن باشند.
- Jobهای RUNNING مطابق recovery policy بازیابی شوند.

### 10. کنترل orphan شدن Job/Run

در API فعلی Job و Run جدا ایجاد می‌شوند.

Stage 33 باید failure path شفاف داشته باشد تا اگر ساخت یکی از آن‌ها شکست خورد، silent orphan ایجاد نشود.

Atomic creation کامل در Stage 40 نهایی می‌شود، ولی Stage 33 باید رفتار خطا را صریح و قابل مشاهده کند.

---

## چیزهایی که در Stage 33 انجام نمی‌دهیم

- اتصال live به Google Search Console
- OAuth refresh کامل
- Baseline واقعی
- SERP provider واقعی
- LLM production
- اجرای واقعی تغییر روی سایت
- UI
- multi-worker distributed locking
- observability exporter دائمی

---

# تست‌های Stage 33

## Configuration

- نبود API key → failure
- API key خالی → failure
- نبود DSN در حالت production → failure
- parse صحیح worker settings

## Composition

- repository صحیح ساخته می‌شود
- سه Store از یک repository استفاده می‌کنند
- SEO handler ثبت شده است
- dependency graph کامل است

## Worker

- worker handler واقعی را resolve می‌کند
- startup recovery انجام می‌شود
- shutdown loop را متوقف می‌کند

## End-to-End Wiring

- API → Job → Worker → Run
- successful run
- failed run
- retry
- observability context preservation

## Persistence

- create باقی می‌ماند
- update versioned باقی می‌ماند
- restart اطلاعات را حذف نمی‌کند
- recovery بعد از restart کار می‌کند

## Security

- `change-me` وجود ندارد
- plaintext secret وارد persistence نمی‌شود

---

# معیار پذیرش نهایی Stage 33

Stage 33 فقط وقتی Done است که:

- Application با configuration واقعی بالا بیاید.
- Repository اصلی PostgreSQL باشد.
- API key بدون fallback اجباری باشد.
- SEO Run Handler در registry واقعی ثبت شده باشد.
- Worker واقعی queue را consume کند.
- API request واقعاً اجرا شود.
- Job و Run نتیجه صحیح بگیرند.
- Restart باعث از دست رفتن اطلاعات نشود.
- Recovery کار کند.
- Observability regression نداشته باشد.
- تست‌های قبلی سبز باقی بمانند.

---

# بخش دوم — Roadmap نهایی پروژه از Stage 33 تا 43

## 33 — اتصال ورودی واقعی برنامه

**کار:** configuration مرکزی، PostgreSQL persistence، composition root، ثبت SEO handler، worker، wiring واقعی `SEORunService`، حذف `change-me`، startup/shutdown، restart test.

**معیار پایان:** درخواست API واقعاً اجرا شود و بعد از restart اطلاعات باقی بماند.

---

## 34 — اتصال واقعی Search Console

**کار:** اتصال `GSCClient` به `GSCDataGateway`، credential resolution، auth/refresh متناسب با روش ورود، دریافت و ذخیره داده، raw response، normalized observations، BaselineProvider واقعی، snapshot/data snapshot.

**معیار پایان:** داده یک سایت واقعی و بازه مقایسه از ابتدا تا Pipeline عبور کند.

---

## 35 — تثبیت صحت تصمیم‌گیری

**کار:** بررسی تفکیک URL/query، date windows، داده ناقص و duplicate، reconciliation، اصلاح جهت یادگیری Position، deterministic replay، مقایسه live با replay و edge-caseهای داده واقعی.

**معیار پایان:** تصمیم با داده واقعی قابل توضیح و قابل بازسازی باشد.

---

## 36 — تکمیل مسیر پیشنهاد عنوان

**کار:** Page Data، SERP Investigation، SERP Decision، Recommendation، Reasoning Router، provider واقعی LLM، structured output validation، fallback، مصرف/بودجه، telemetry و persistence خروجی.

**معیار پایان:** برای یک فرصت واقعی، پیشنهاد عنوان همراه دلیل و evidence ذخیره شود و failure provider وضعیت مشخص داشته باشد.

---

## 37 — تکمیل مدیریت اقدام و تأیید

**کار:** Action persistence، API مشاهده، approve/reject، history، version-aware approval، جلوگیری از duplicate approval، اتصال Action به Run.

**معیار پایان:** کاربر همان proposal/version مشخص را ببیند و صریحاً برای اجرا تأیید کند.

---

## 38 — اجرای واقعی روی سایت

**کار:** adapter واقعی، نگهداری previous value، concurrency check، idempotency پایدار، verification، result persistence، rollback، failure classification و جلوگیری از اجرای دوباره.

**معیار پایان:** یک تغییر تأییدشده اعمال، verify و در صورت نیاز rollback شود.

---

## 39 — تکمیل چرخه سنجش و یادگیری

**کار:** scheduling measurement، انتظار recrawl، دریافت window جدید، Measurement، Outcome، feedback storage، Learning Context، استفاده advisory در تصمیم بعدی، handling کمبود داده.

**معیار پایان:** هر action اجراشده به نتیجه قابل پیگیری برسد و کمبود داده explicit باشد.

---

## 40 — پایداری Job و Run

**کار:** atomic Job/Run creation، cancellation coordination، retry policy، backoff، failure classification، crash recovery، idempotent retry و تعیین رسمی single-worker یا multi-worker.

**معیار پایان:** قطع process و retry باعث گم‌شدن کار، state متناقض یا اجرای ناخواسته دوباره نشود.

**تصمیم MVP:** single-worker باقی می‌ماند مگر اینکه نیاز واقعی به scale مشخص شود؛ contracts از ابتدا باید قابلیت ارتقا داشته باشند.

---

## 41 — Observability عملیاتی

**کار:** durable event storage/export، endpoint مشاهده، trace/correlation بین Jobهای جدا، trace propagation بعد از human approval، مشاهده مصرف provider/model، retention و error visibility.

**معیار پایان:** یک درخواست واقعی حتی بعد از restart از دریافت تا نتیجه قابل trace باشد.

---

## 42 — مسیر استفاده کاربر

**کار:** UI حداقلی برای ثبت سایت، connection state، ساخت تحلیل، مشاهده Run، proposal، approve/reject، execution، measurement و خطاهای قابل اقدام.

**معیار پایان:** استفاده روزمره بدون دستکاری database یا اجرای دستی Python ممکن باشد.

---

## 43 — استقرار و آزمون پذیرش

**کار:** production configuration، نهایی‌سازی dependencyها، migration، secret management، backup/restore، readiness/health، monitoring، worker deployment، controlled rollout، real-site acceptance، failure/recovery drills و security review.

**معیار پایان:** کل مسیر با سرویس‌های واقعی کار کند و سناریوهای اصلی failure/recovery نیز آزمایش شده باشند.

---

# تعریف Done نهایی محصول

محصول زمانی MVP واقعی محسوب می‌شود که فقط audit تولید نکند، بلکه چرخه زیر را به شکل قابل اعتماد اجرا کند:

`Observe → Understand → Diagnose → Decide → Act → Measure → Learn`

هر حلقه باید دارای:

- evidence
- confidence
- snapshot
- replayability
- state machine
- observability
- human approval برای action پرریسک
- rollback
- measurement
- feedback

باشد.

## ترتیب تثبیت‌شده توسعه

`32c Observability Lifecycle`
→ `33 Runtime Wiring`
→ `34 Real GSC`
→ `35 Decision Integrity`
→ `36 Title Recommendation + LLM`
→ `37 Approval`
→ `38 Real Execution`
→ `39 Measurement + Learning`
→ `40 Job/Run Reliability`
→ `41 Operational Observability`
→ `42 User Experience`
→ `43 Production + Acceptance`

این ترتیب حفظ می‌شود تا هیچ لایه‌ای قبل از قابل اعتماد بودن لایه زیرین وارد production نشود.
