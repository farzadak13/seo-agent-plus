"""Session-level lock: only one embedded scheduler may recover/run a database."""
from contextlib import contextmanager


@contextmanager
def postgres_worker_lease(repository):
    with repository._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(733033) AS acquired")
            row = cursor.fetchone()
            acquired = row['acquired'] if isinstance(row, dict) else row[0]
        if not acquired:
            raise RuntimeError("Another SEO worker already owns this PostgreSQL database.")
        connection.commit()
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(733033)")
            connection.commit()
