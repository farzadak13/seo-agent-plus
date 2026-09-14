# Stage30 — API + Authentication + Jobs/Scheduler

## Added

- `app/models/jobs.py`: durable job contract and lifecycle.
- `app/jobs/store.py`: repository-backed job persistence.
- `app/jobs/scheduler.py`: single-worker scheduler with restart recovery.
- `app/api/auth.py`: Bearer API-key authentication using PBKDF2 + constant-time comparison.
- `app/api/schemas.py`: API request/response contracts.
- `app/api/app.py`: FastAPI application factory.
- `app/api/main.py`: local runnable service with an `echo` handler for smoke testing.
- Stage30 unit tests for authentication, persistence, scheduler, and API.
- `requirements-stage30.txt`.

## API

Unauthenticated:

- `GET /healthz`

Authenticated:

- `POST /v1/jobs`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`

## Important architectural boundary

Stage30 deliberately does not place SEO decision logic inside the API or scheduler. Jobs receive a `job_type` and payload, then a registered handler performs the domain work.

The local `app/api/main.py` registers only `echo`. The real Decision Pipeline handler should be wired during onboarding/configuration rather than hidden inside the API layer.

## Run locally

PowerShell:

```powershell
python -m pip install -r requirements-stage30.txt
$env:SEO_AGENT_API_KEY = "replace-with-a-long-random-secret"
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

Create a job:

```powershell
$headers = @{ Authorization = "Bearer replace-with-a-long-random-secret" }
$body = @{ job_type = "echo"; payload = @{ hello = "world" } } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/v1/jobs -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

The API process itself does not automatically poll the scheduler. In tests and later production deployment, a scheduler/worker lifecycle should call `JobScheduler.run_once()` or `run_forever()` using the same persistence backend.

## Tests

```powershell
pytest -q tests\unit\test_stage30_auth.py tests\unit\test_stage30_jobs.py tests\unit\test_stage30_scheduler.py tests\unit\test_stage30_api.py
pytest -q
python -m compileall app tests -q
python -m pip check
```
