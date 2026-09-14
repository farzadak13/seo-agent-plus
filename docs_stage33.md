# Stage 33 — Runtime Wiring

This package switches the application composition root to a PostgreSQL-backed repository, registers the real SEO run handler, adds a controlled single-worker runtime lifecycle, and removes the insecure API-key fallback.

Run locally:

```powershell
$env:SEO_AGENT_API_KEY = "<strong-random-key>"
$env:SEO_AGENT_DATABASE_DSN = "postgresql://..."
$env:SEO_AGENT_GSC_MODE = "stub"
uvicorn app.api.main:app --factory --host 127.0.0.1 --port 8000
```

For Stage 33, `SEO_AGENT_GSC_MODE=stub` is explicit and temporary. Live GSC wiring belongs to Stage 34.

Validation:

```powershell
pytest -q tests\unit\test_stage33_runtime.py tests\unit\test_stage33_restart_wiring.py
pytest -q -m "not integration"
python -m compileall app tests -q
python -m pip check
```
