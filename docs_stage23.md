# Stage 23 — PostgreSQL Persistence

## Scope

This stage introduces a real PostgreSQL adapter behind the existing repository
contract.

The domain/decision layer remains database-agnostic.

## Production dependency

Install psycopg 3 with the binary package:

    pip install "psycopg[binary]>=3.2,<4"

The project can keep using the in-memory repository for unit tests that do not
need a PostgreSQL server.

## Database

Apply:

    migrations/001_persistence_foundation.sql

The table is append-only. A replacement inserts a new version rather than
overwriting an existing row.

## Connection

Use a PostgreSQL DSN, for example:

    postgresql://USER:PASSWORD@HOST:5432/DBNAME

Do not commit credentials. Store the DSN in the deployment environment.

## Important semantics

- `get()` returns the newest version.
- `list()` returns the newest version of each aggregate.
- `replace()` requires the exact current version and inserts `expected_version + 1`.
- Historical versions remain in the table.
- The DB adapter does not execute SEO logic.
- The DB adapter does not call LLM providers.
- Replay remains a pure application-layer operation.
