# Stage31 — Onboarding + Site Configuration + Run Orchestration

## هدف
این مرحله API را از Job-only به یک لایه Onboarding تبدیل می‌کند و یک قرارداد صریح برای اجرای SEO Run از طریق Scheduler می‌سازد.

## فایل‌های جدید
- `app/models/sites.py`
- `app/onboarding/secrets.py`
- `app/onboarding/site_store.py`
- `app/models/runs.py`
- `app/runs/contracts.py`
- `app/runs/service.py`
- `app/runs/runner.py`
- `app/runs/store.py`
- `app/runs/handler.py`

## فایل‌های جایگزین
- `app/api/schemas.py`
- `app/api/app.py`
- `app/api/main.py`

## امنیت credential
Site فقط SecretRef نگه می‌دارد. مقدار واقعی credential از EnvironmentSecretResolver در زمان اجرای Run خوانده می‌شود و در Persistence ذخیره نمی‌شود.

## API
- `POST /v1/sites`
- `GET /v1/sites/{site_id}`
- `PUT /v1/sites/{site_id}/connections/gsc`
- `PUT /v1/sites/{site_id}/connections/site-adapter`
- `POST /v1/sites/{site_id}/runs`
- `GET /v1/runs/{run_id}`

## مرز معماری
`SEORunService` فقط orchestrate می‌کند؛ transport، secret resolution، baseline loading و pipeline execution از طریق Protocol تزریق می‌شوند.

## نکته مهم
`app/api/main.py` عمداً یک GSC Gateway واقعی را hard-code نمی‌کند. برای اجرای live باید Gateway واقعی Stage24 به `GSCDataGateway` متصل شود. این کار عمداً از API جدا نگه داشته شده تا API به Brain سیستم تبدیل نشود.
