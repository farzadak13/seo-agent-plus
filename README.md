# AI SEO Agent

A deterministic SEO decision engine: it observes Search Console data,
diagnoses opportunities by rule, and only then asks a language model to write
a title. The decision is explainable and replayable without the model.

## Requirements

- Python 3.14
- Docker (for the local PostgreSQL)

## Getting started

```powershell
docker compose up -d
Copy-Item .env.example .env      # then set SEO_AGENT_API_KEY
python -m app.migrate
pytest -q -m "not integration"
```

Full instructions, including how to load `.env` in PowerShell and how to run
the live database tests, are in [docs/database.md](docs/database.md).

## Layout

| Path | What lives there |
| --- | --- |
| `app/models` | Domain models. No I/O. |
| `app/detectors`, `app/classifier`, `app/engine`, `app/opportunity`, `app/strategy` | Deterministic decision path |
| `app/serp`, `app/title`, `app/reasoning` | SERP evidence and title proposals |
| `app/execution`, `app/site_adapters` | Applying changes to a site |
| `app/measurement`, `app/outcome`, `app/learning` | Measuring what a change did |
| `app/persistence` | Append-only versioned record store and migrations |
| `app/runtime` | Composition root: config, container, worker |
| `migrations` | Numbered SQL, applied by `python -m app.migrate` |

## Testing

```powershell
pytest -q -m "not integration"   # no external services
pytest -q -m integration         # needs SEO_AGENT_TEST_DSN and ARVAN_AI_ENDPOINT
```
