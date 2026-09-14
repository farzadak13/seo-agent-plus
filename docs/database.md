# Database setup

The application has no in-memory production mode. `PostgresRepository` is the
only repository the runtime builds, so a database must exist before the API
starts, before `pytest -m integration` can run, and before any claim about
"data survives restart" can be tested.

## Start PostgreSQL

```powershell
docker compose up -d
docker compose ps          # postgres should report (healthy)
```

This creates two databases on first start: `seoagent` for development and
`seoagent_test` for integration tests. They are separate on purpose — the
integration suite drops its tables.

The container publishes **port 55432**, not the default 5432. Any PostgreSQL
installed directly on the machine keeps 5432 and is left alone; this project
talks only to the container. If `docker compose up` reports that the port is
unavailable, something else has taken 55432 — find it with:

```powershell
Get-NetTCPConnection -LocalPort 55432 -State Listen
```

To stop it, keeping the data:

```powershell
docker compose stop
```

To destroy the data and start clean:

```powershell
docker compose down -v
```

## Configure the environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace `SEO_AGENT_API_KEY` with a long random string. To
generate one:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

PowerShell does not read `.env` on its own. Load it into the current session:

```powershell
Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "env:$($name.Trim())" -Value $value.Trim()
}
```

Verify:

```powershell
python -c "import os; print(os.environ['SEO_AGENT_DATABASE_DSN'])"
```

## Apply migrations

Migrations are a deliberate step, not something the application does on
startup: a bad migration applied automatically takes the service down with it.

```powershell
python -m app.migrate --status   # what exists on disk
python -m app.migrate            # apply what the database is missing
```

Running it again is safe — already-applied migrations are recorded in
`schema_migrations` and skipped. Editing a migration that has already been
applied is refused; add a new numbered file instead.

## Run the tests

```powershell
pytest -q -m "not integration"   # no database needed
pytest -q -m integration         # needs SEO_AGENT_TEST_DSN
```

If `SEO_AGENT_TEST_DSN` is unset the integration module is skipped. If it is
set but unreachable, the module is skipped with the connection error in the
reason rather than failing every test with the same traceback.

## Start the application

```powershell
uvicorn app.api.main:app_factory --factory --host 127.0.0.1 --port 8000
```

Check it:

```powershell
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

`/readyz` reports persistence reachability, whether the worker is running, and
whether the title path is wired — it is the quickest way to see what this
instance can actually do.

## Inspecting the database

The image ships `psql`, so no local client install is needed:

```powershell
docker compose exec postgres psql -U seoagent -d seoagent
```

Useful queries:

```sql
SELECT version, name, applied_at FROM schema_migrations ORDER BY version;
SELECT aggregate_type, count(*) FROM persistence_records GROUP BY 1;
SELECT tenant_id, count(*) FROM persistence_records GROUP BY 1;
```

## Deployment note

Nothing in this file describes a deployment. A deployed database needs a
password from a secret store, TLS, backups, and a restore that has actually
been tested. Those belong to the production stage, not here.
