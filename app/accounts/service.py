"""Signing in, recognising a signed-in browser, and signing out.

The session token is 256 bits from ``secrets`` and is given to the browser
once, in a cookie; only its SHA-256 is stored, so a copy of the database does
not contain anything that works as a session. Sessions end after a fixed
lifetime whatever happens, sooner when idle, at once when signed out, and for
good when the operator disables the account, changes its password, unlinks
its Google identity or revokes its sessions (the account's session
generation moves on, and a session counts only under its own). A session also
counts only while its tenant is active: suspending a customer shuts their
browsers out exactly as it shuts out their API keys.

Every refusal to sign in says the same thing. Which of "no such account",
"wrong password" or "disabled" applied is information about someone else.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.accounts.models import Account, AccountStatus, LoginMethod, Session
from app.accounts.passwords import hash_password, needs_rehash, verify_password
from app.accounts.store import (
    AccountChangedError,
    AccountExistsError,
    AccountStore,
    SessionStore,
    normalize_email,
)
from app.models.tenants import TenantStatus
from app.persistence.contracts import PersistenceConflictError
from app.tenancy.store import TenantStore

SESSION_LIFETIME = timedelta(days=14)
IDLE_TIMEOUT = timedelta(days=3)
# last_seen is written at most this often, not on every request.
TOUCH_EVERY = timedelta(minutes=10)

GENERIC_REFUSAL = "The email or password is not correct."
GOOGLE_REFUSAL = "There is no HoshyarSEO account for this Google account."
# Said only to someone who has just proved who they are.
SUSPENDED = "This account is suspended. Contact HoshyarSEO support."


class SignInError(Exception):
    """Sign-in refused. The message is safe to show."""


class TooManyAttemptsError(SignInError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def address_key(address: str) -> str:
    """What one client's attempts are counted under.

    An IPv4 address as it is. An IPv6 address by its /64: one subscriber is
    routinely given a whole /64, and counting each address in it separately
    would let them start every attempt from a fresh one. An IPv4 address
    written as IPv6 (::ffff:1.2.3.4) counts as itself.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


class AttemptLimiter:
    """Events per key in a sliding window, in this process's memory.

    The service runs as one process (the worker's lease enforces it), so one
    process's memory is the whole picture. A restart forgets the counts,
    which only ever favours a legitimate user.

    Memory stays bounded: looking a key up never stores it, a key whose
    events have all aged out is dropped, and past ``max_keys`` the oldest
    keys go first. Otherwise every email or address ever tried, however
    random, would stay in memory for good.
    """

    def __init__(
        self, *, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _expire(self, key: str, now: float) -> deque | None:
        events = self._events.get(key)
        if events is None:
            return None
        while events and now - events[0] > self._window:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events

    def blocked(self, key: str) -> bool:
        with self._lock:
            events = self._expire(key, self._clock())
            return events is not None and len(events) >= self._limit

    def record(self, key: str) -> None:
        with self._lock:
            self._append(key, self._clock())

    def attempt(self, key: str) -> bool:
        """Count an attempt, unless the key is already at its limit: then False.

        One step under the lock, so requests arriving together cannot all see
        "not blocked" before any of them is counted.
        """
        with self._lock:
            now = self._clock()
            events = self._expire(key, now)
            if events is not None and len(events) >= self._limit:
                return False
            self._append(key, now)
            return True

    def cancel(self, key: str) -> None:
        """Take back one counted attempt (one that turned out to succeed)."""
        with self._lock:
            events = self._events.get(key)
            if events:
                events.pop()
                if not events:
                    del self._events[key]

    def _append(self, key: str, now: float) -> None:
        events = self._expire(key, now)
        if events is None:
            if len(self._events) >= self._max_keys:
                self._prune(now)
            events = self._events[key] = deque()
        events.append(now)

    # Kept for readability at the call sites that count failures.
    failed = record

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def __len__(self) -> int:
        return len(self._events)

    def _prune(self, now: float) -> None:
        for key in list(self._events):
            self._expire(key, now)
        overflow = len(self._events) - self._max_keys + 1
        if overflow > 0:
            for key in sorted(self._events, key=lambda k: self._events[k][-1])[:overflow]:
                del self._events[key]


class AuthService:
    def __init__(
        self,
        *,
        accounts: AccountStore,
        sessions: SessionStore,
        tenants: TenantStore,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        per_email: AttemptLimiter | None = None,
        per_address: AttemptLimiter | None = None,
    ) -> None:
        self._accounts = accounts
        self._sessions = sessions
        self._tenants = tenants
        self._clock = clock
        # "is None", not "or": an empty limiter has length 0 and is falsy.
        self._per_email = AttemptLimiter(limit=8, window_seconds=15 * 60) if per_email is None else per_email
        self._per_address = AttemptLimiter(limit=40, window_seconds=15 * 60) if per_address is None else per_address

    # --- signing in -----------------------------------------------------------

    def sign_in_with_password(self, *, email: str, password: str, address: str) -> tuple[str, Session]:
        email = normalize_email(email)
        address = address_key(address)
        # Counted before the password is checked, not after: checking takes a
        # while, and attempts counted only once they had failed would let as
        # many guesses through at once as there are threads to run them.
        if not self._per_email.attempt(email):
            raise TooManyAttemptsError("Too many attempts. Wait a few minutes and try again.")
        if not self._per_address.attempt(address):
            self._per_email.cancel(email)
            raise TooManyAttemptsError("Too many attempts. Wait a few minutes and try again.")
        account = self._accounts.by_email(email)
        stored = account.password_hash if account else None
        if not verify_password(password, stored) or account.status != AccountStatus.ACTIVE:
            raise SignInError(GENERIC_REFUSAL)
        # Right: the email's count starts over, and this attempt is not held
        # against the address, where only failures count.
        self._per_email.succeeded(email)
        self._per_address.cancel(address)
        if needs_rehash(account.password_hash):
            try:
                account = self._accounts.update(
                    account.model_copy(update={"password_hash": hash_password(password)})
                )
            except (AccountChangedError, ValueError):
                # Changed meanwhile, or the password no longer meets a policy
                # raised since it was set (WeakPasswordError): the rehash waits.
                # Never worth refusing a correct password over.
                pass
        checked_hash = account.password_hash
        return self._open(
            account, LoginMethod.PASSWORD,
            still_valid=lambda current: current.password_hash == checked_hash,
        )

    def sign_in_with_google(self, *, subject: str, email: str | None, email_verified: bool) -> tuple[str, Session]:
        """Only an account the operator created: matched by Google id, or once by verified email."""
        account = self._accounts.by_google_subject(subject)
        if account is None:
            if not email or not email_verified:
                raise SignInError("Google did not confirm an email address for this account.")
            account = self._accounts.by_email(email)
            if account is None or account.google_subject not in (None, subject):
                raise SignInError(GOOGLE_REFUSAL)
            # Unlinked by the operator: it does not link itself again by email.
            if not account.google_link_allowed:
                raise SignInError(GOOGLE_REFUSAL)
            # Checked before linking: a disabled account must not collect a
            # Google identity it cannot use, and that would stay after enabling.
            if account.status != AccountStatus.ACTIVE:
                raise SignInError(GOOGLE_REFUSAL)
            self._require_active_tenant(account)
            try:
                account = self._accounts.bind_google(account, subject)
            except (AccountExistsError, AccountChangedError) as exc:
                raise SignInError(GOOGLE_REFUSAL) from exc
        if account.status != AccountStatus.ACTIVE:
            raise SignInError(GOOGLE_REFUSAL)
        return self._open(
            account, LoginMethod.GOOGLE,
            still_valid=lambda current: current.google_subject == subject,
        )

    def _open(
        self, account: Account, method: LoginMethod, *, still_valid: Callable[[Account], bool]
    ) -> tuple[str, Session]:
        """Open a session under the account's generation, then confirm it still holds.

        The account is read again after the session exists. If the operator's
        change landed first, the new generation, status or credential is
        visible now and the session is revoked at once. If it lands after,
        the session carries the old generation and stops counting then. There
        is no gap between the two.
        """
        self._require_active_tenant(account)
        now = self._clock()
        token = secrets.token_urlsafe(32)
        session = self._sessions.create(
            Session(
                token_hash=token_hash(token),
                account_id=account.account_id,
                tenant_id=account.tenant_id,
                method=method,
                generation=account.session_generation,
                csrf_token=secrets.token_urlsafe(32),
                created_at=now,
                expires_at=now + SESSION_LIFETIME,
                last_seen_at=now,
            )
        )
        current = self._accounts.get(account.account_id)
        if (
            current is None
            or current.status != AccountStatus.ACTIVE
            or current.session_generation != session.generation
            or not still_valid(current)
        ):
            self._sessions.replace(session.model_copy(update={"revoked_at": now}), expected_version=1)
            raise SignInError(GENERIC_REFUSAL if method == LoginMethod.PASSWORD else GOOGLE_REFUSAL)
        try:
            self._accounts.update(current.model_copy(update={"last_login_at": now}))
        except AccountChangedError:
            pass  # a bookkeeping field; never worth overwriting someone else's change
        return token, session

    # --- recognising a signed-in browser ------------------------------------------

    def session_for(self, token: str | None) -> tuple[Session, Account] | None:
        if not token or len(token) > 200:
            return None
        found = self._sessions.get(token_hash(token))
        if found is None:
            return None
        session, version = found
        now = self._clock()
        if session.revoked_at or now >= session.expires_at or now - session.last_seen_at >= IDLE_TIMEOUT:
            return None
        account = self._accounts.get(session.account_id)
        if account is None or account.status != AccountStatus.ACTIVE:
            return None
        if session.generation != account.session_generation:
            return None
        if not self._tenant_active(session.tenant_id):
            return None
        if now - session.last_seen_at >= TOUCH_EVERY:
            try:
                session = self._sessions.replace(
                    session.model_copy(update={"last_seen_at": now}), expected_version=version
                )
            except PersistenceConflictError:
                pass  # a concurrent request touched it first; the session is still good
        return session, account

    def _tenant_active(self, tenant_id: str) -> bool:
        tenant = self._tenants.find(tenant_id)
        return tenant is not None and tenant.status == TenantStatus.ACTIVE

    def _require_active_tenant(self, account: Account) -> None:
        """Checked once the person has proved who they are, so it may say why."""
        if not self._tenant_active(account.tenant_id):
            raise SignInError(SUSPENDED)

    def sign_out(self, token: str | None) -> None:
        if not token:
            return
        found = self._sessions.get(token_hash(token))
        if found is None or found[0].revoked_at:
            return
        session, version = found
        self._sessions.replace(session.model_copy(update={"revoked_at": self._clock()}), expected_version=version)

    def account(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    # --- the operator's commands --------------------------------------------------

    def end_sessions(self, account: Account, **changes) -> Account:
        """Apply the operator's change and end every session opened before it, in one write."""
        return self._accounts.update(
            account.model_copy(update={**changes, "session_generation": account.session_generation + 1})
        )

    def unlink_google(self, account: Account) -> Account:
        """No Google identity signs in to the account any more, not even by
        linking itself again by email, and every session ends: one write."""
        return self._accounts.unlink_google(account, session_generation=account.session_generation + 1)

    def allow_google(self, account: Account) -> Account:
        """The operator lets a Google account with this email link itself again."""
        return self._accounts.update(account.model_copy(update={"google_link_allowed": True}))

    def create_account(self, *, tenant_id: str, email: str, password: str | None) -> Account:
        account = Account(
            account_id="u-" + secrets.token_hex(8),
            tenant_id=tenant_id,
            email=normalize_email(email),
            password_hash=hash_password(password) if password else None,
        )
        return self._accounts.create(account)
