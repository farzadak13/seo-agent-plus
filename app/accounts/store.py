"""Accounts and sessions in the shared record store.

An account is found by its id, by its email, or by its Google identity. The
last two are small index records whose ids *are* the email and the Google
subject: the store refuses a second record with the same id, so two accounts
can never share an email or a Google identity, whatever runs concurrently.
An unlinked Google identity keeps its index record, emptied, so the same
identity can be linked again later, to an account in any tenant: that record
belongs to no tenant (a record never changes tenant once written), only its
payload says which account it points to.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.accounts.models import Account, AccountStatus, Session
from app.models.persistence import PersistenceRecord
from app.persistence.contracts import PersistenceConflictError, Repository

ACCOUNT = "account"
ACCOUNT_BY_EMAIL = "account_email"
ACCOUNT_BY_GOOGLE = "account_google"
SESSION = "session"


class AccountExistsError(PersistenceConflictError):
    pass


class AccountChangedError(PersistenceConflictError):
    """The account was changed by someone else since this copy was read."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AccountStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def _transaction(self):
        """The repository's transaction, where it has one (the in-memory one does not).

        Inside it, a write the database refuses rolls back everything before
        it, so a conflict must end the transaction, never be caught within it.
        """
        transaction = getattr(self._repository, "transaction", None)
        return transaction() if callable(transaction) else _null()

    def create(self, account: Account) -> Account:
        with self._transaction():
            try:
                self._repository.create(_index(ACCOUNT_BY_EMAIL, account.email, account.account_id, account.tenant_id))
            except PersistenceConflictError:
                raise AccountExistsError(f"An account with this email already exists: {account.email}") from None
            self._repository.create(_record(ACCOUNT, account.account_id, account, version=1))
        return account

    def get(self, account_id: str) -> Account | None:
        record = self._repository.get(aggregate_type=ACCOUNT, aggregate_id=account_id)
        return None if record is None else Account.model_validate(record.payload)

    def by_email(self, email: str) -> Account | None:
        index = self._repository.get(aggregate_type=ACCOUNT_BY_EMAIL, aggregate_id=normalize_email(email))
        return None if index is None else self.get(index.payload["account_id"])

    def by_google_subject(self, subject: str) -> Account | None:
        index = self._repository.get(aggregate_type=ACCOUNT_BY_GOOGLE, aggregate_id=subject)
        if index is None or not index.payload.get("account_id"):
            return None
        return self.get(index.payload["account_id"])

    def bind_google(self, account: Account, subject: str) -> Account:
        """Link a Google identity, once. Refused if that identity is anyone's already.

        The index record and the account's field are written in one
        transaction: an index pointing at an account that does not know it
        would refuse that account's Google sign-in for good, with nothing in
        the CLI able to repair it. The account is read afresh on each try, so
        a change made meanwhile (a sign-in stamping last_login_at) is not lost.

        Whether the account may take the identity is decided on that fresh
        read, not on the caller's copy: the operator may have disabled the
        account, turned Google off or linked another identity since, and the
        link must not undo that. Refused, nothing is written. A change landing
        after that read is caught by update's guard, and the whole link is
        rolled back and decided again.
        """
        for _ in range(3):
            try:
                with self._transaction():
                    current = self.get(account.account_id)
                    if (
                        current is None
                        or current.status != AccountStatus.ACTIVE
                        or not current.google_link_allowed
                        or current.google_subject not in (None, subject)
                    ):
                        raise AccountExistsError("This account cannot take this Google account.")
                    self._claim_google(account.account_id, subject)
                    return self.update(current.model_copy(update={"google_subject": subject}))
            except AccountChangedError:
                continue  # the account moved; the whole link was rolled back, try again
        raise AccountChangedError(f"account kept changing: {account.account_id}")

    def _claim_google(self, account_id: str, subject: str) -> None:
        index = self._repository.get(aggregate_type=ACCOUNT_BY_GOOGLE, aggregate_id=subject)
        taken = AccountExistsError("This Google account is already linked to an account.")
        if index is not None and index.payload.get("account_id"):
            raise taken
        try:
            if index is None:
                self._repository.create(_index(ACCOUNT_BY_GOOGLE, subject, account_id, None))
            else:
                # Free (unlinked before): taken over, guarded by its version so
                # two takers cannot both succeed.
                self._repository.replace(
                    _index(ACCOUNT_BY_GOOGLE, subject, account_id, None, version=index.version + 1),
                    expected_version=index.version,
                )
        except PersistenceConflictError:
            raise taken from None

    def unlink_google(self, account: Account, **changes) -> Account:
        """Cut the account off from Google sign-in, in one transaction.

        Its Google index record is emptied (the identity no longer finds the
        account), and linking by email is turned off (it cannot find its way
        back either). ``changes`` are written in the same update.
        """
        with self._transaction():
            if account.google_subject:
                index = self._repository.get(aggregate_type=ACCOUNT_BY_GOOGLE, aggregate_id=account.google_subject)
                if index is not None and index.payload.get("account_id") == account.account_id:
                    try:
                        self._repository.replace(
                            _index(ACCOUNT_BY_GOOGLE, account.google_subject, None, None,
                                   version=index.version + 1),
                            expected_version=index.version,
                        )
                    except PersistenceConflictError:
                        raise AccountChangedError(f"account changed while being written: {account.account_id}") from None
            return self.update(
                account.model_copy(update={"google_subject": None, "google_link_allowed": False, **changes})
            )

    def update(self, account: Account) -> Account:
        """Write a changed copy, only if nothing else changed the account since it was read.

        Every write stamps updated_at, so the stamp a copy carries says which
        version it was read from. Without this, a sign-in that read the
        account before the operator disabled it would write "active" back.
        A write landing between the check and the replace is refused by the
        repository's version guard; both surface as AccountChangedError.
        """
        current = self._repository.get(aggregate_type=ACCOUNT, aggregate_id=account.account_id)
        if current is None:
            raise LookupError(f"account not found: {account.account_id}")
        if Account.model_validate(current.payload).updated_at != account.updated_at:
            raise AccountChangedError(f"account changed since it was read: {account.account_id}")
        written = account.model_copy(update={"updated_at": _later_than(account.updated_at)})
        try:
            self._repository.replace(
                _record(ACCOUNT, account.account_id, written, version=current.version + 1),
                expected_version=current.version,
            )
        except PersistenceConflictError:
            raise AccountChangedError(f"account changed while being written: {account.account_id}") from None
        return written

    def list_for_tenant(self, tenant_id: str) -> list[Account]:
        return [
            Account.model_validate(record.payload)
            for record in self._repository.list(aggregate_type=ACCOUNT)
            if record.tenant_id == tenant_id
        ]


class SessionStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, session: Session) -> Session:
        self._repository.create(_record(SESSION, session.token_hash, session, version=1))
        return session

    def get(self, token_hash: str) -> tuple[Session, int] | None:
        record = self._repository.get(aggregate_type=SESSION, aggregate_id=token_hash)
        return None if record is None else (Session.model_validate(record.payload), record.version)

    def replace(self, session: Session, *, expected_version: int) -> Session:
        self._repository.replace(
            _record(SESSION, session.token_hash, session, version=expected_version + 1),
            expected_version=expected_version,
        )
        return session


def _later_than(previous: datetime) -> datetime:
    """Now, or a microsecond after the previous stamp if the clock has not moved:
    two writes must never carry the same stamp."""
    now = datetime.now(timezone.utc)
    return now if now > previous else previous + timedelta(microseconds=1)


def _record(kind: str, aggregate_id: str, model, *, version: int) -> PersistenceRecord:
    return PersistenceRecord(
        record_id=f"{kind}:{aggregate_id}:v{version}",
        aggregate_type=kind,
        aggregate_id=aggregate_id,
        version=version,
        payload=model.model_dump(mode="json"),
        tenant_id=model.tenant_id,
    )


def _index(kind: str, key: str, account_id: str | None, tenant_id: str | None, *, version: int = 1) -> PersistenceRecord:
    return PersistenceRecord(
        record_id=f"{kind}:{key}:v{version}",
        aggregate_type=kind,
        aggregate_id=key,
        version=version,
        payload={"account_id": account_id},
        tenant_id=tenant_id,
    )


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
