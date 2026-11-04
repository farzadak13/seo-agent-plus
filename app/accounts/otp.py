"""A one-time code by SMS: what proves a mobile number is the person's.

Each code costs us an SMS and can be sent to anyone's phone, so sending is
limited three ways: a wait between two codes for one number, a daily number of
codes for one number, and an hourly number of requests from one address.
Guessing is limited too: a code takes five tries and lives two minutes, so a
six-digit code is found by chance about once in 200,000 codes.

Only a hash of the code is stored, with its own salt.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.accounts.phone import to_ascii_digits
from app.accounts.service import AttemptLimiter, address_key
from app.models.persistence import PersistenceRecord
from app.notify.sms import SmsError, SmsSender
from app.persistence.contracts import PersistenceConflictError, Repository

log = logging.getLogger(__name__)

OTP_AGGREGATE = "otp_challenge"
CODE_LIFETIME = timedelta(minutes=2)
RESEND_AFTER = timedelta(seconds=60)
CODES_PER_DAY = 5
TRIES_PER_CODE = 5
# Once a right code has been shown and kept for sign-up, the form takes time.
SIGNUP_FORM_TIME = timedelta(minutes=10)
# Every SMS costs money, whoever asks: the whole service stops sending past this.
SMS_PER_HOUR = 300


class OtpError(Exception):
    """Refused. The message is safe to show."""


class OtpTooSoon(OtpError):
    def __init__(self, message: str, wait_seconds: int) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


class OtpChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mobile: str
    salt: str
    code_hash: str
    expires_at: datetime
    tries: int = 0
    used: bool = False
    # When codes went to this number, the last day's only.
    sent: list[datetime] = Field(default_factory=list)


def _hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("ascii")).hexdigest()


class OtpService:
    def __init__(
        self,
        *,
        repository: Repository,
        sender: SmsSender,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        per_address: AttemptLimiter | None = None,
        checks_per_address: AttemptLimiter | None = None,
        all_sms: AttemptLimiter | None = None,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._clock = clock
        self._per_address = AttemptLimiter(limit=10, window_seconds=3600) if per_address is None else per_address
        self._checks = AttemptLimiter(limit=30, window_seconds=3600) if checks_per_address is None else checks_per_address
        self._all = AttemptLimiter(limit=SMS_PER_HOUR, window_seconds=3600) if all_sms is None else all_sms

    def _read(self, mobile: str) -> tuple[OtpChallenge | None, int]:
        record = self._repository.get(aggregate_type=OTP_AGGREGATE, aggregate_id=mobile)
        return (OtpChallenge.model_validate(record.payload), record.version) if record else (None, 0)

    def _save(self, challenge: OtpChallenge, version: int) -> None:
        record = PersistenceRecord(
            record_id=f"{OTP_AGGREGATE}:{challenge.mobile}:v{version + 1}",
            aggregate_type=OTP_AGGREGATE,
            aggregate_id=challenge.mobile,
            version=version + 1,
            payload=challenge.model_dump(mode="json"),
        )
        if version:
            self._repository.replace(record, expected_version=version)
        else:
            self._repository.create(record)

    def send(self, *, mobile: str, address: str) -> None:
        """Send a new code to this (already normalised) number."""
        now = self._clock()
        current, version = self._read(mobile)
        sent = [moment for moment in (current.sent if current else []) if now - moment < timedelta(days=1)]
        if sent and now - sent[-1] < RESEND_AFTER:
            wait = int((RESEND_AFTER - (now - sent[-1])).total_seconds()) + 1
            raise OtpTooSoon("A code was sent a moment ago.", wait)
        if len(sent) >= CODES_PER_DAY:
            raise OtpError("Too many codes sent to this number today.")
        if not self._per_address.attempt(address_key(address)):
            raise OtpError("Too many codes requested. Try again later.")
        if not self._all.attempt("all"):
            log.error("sign-in codes: the hourly cap of %s SMS was reached; sending has stopped", SMS_PER_HOUR)
            raise OtpError("Too many codes requested. Try again later.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(8)
        challenge = OtpChallenge(
            mobile=mobile, salt=salt, code_hash=_hash(salt, code),
            expires_at=now + CODE_LIFETIME, sent=[*sent, now],
        )
        try:
            # Saved before it is sent: two requests together cannot both send.
            self._save(challenge, version)
        except PersistenceConflictError as exc:
            raise OtpTooSoon("A code was sent a moment ago.", int(RESEND_AFTER.total_seconds())) from exc
        try:
            self._sender.send_code(mobile=mobile, code=code)
        except SmsError as exc:
            raise OtpError("The code could not be sent. Try again in a few minutes.") from exc

    def check(self, *, mobile: str, code: str, address: str, consume: bool = True) -> bool:
        """Is this the code sent to this number? Every call is a try, right or wrong.

        ``consume=False`` leaves a right code usable once more (sign-up asks
        for the business name after the code, and sends both together).
        """
        if not self._checks.attempt(address_key(address)):
            raise OtpError("Too many tries. Try again later.")
        current, version = self._read(mobile)
        now = self._clock()
        if current is None or current.used or now >= current.expires_at or current.tries >= TRIES_PER_CODE:
            return False
        # The try is counted before the code is compared, and over the version
        # read: a burst of guesses cannot all slip in on one count.
        try:
            self._save(current.model_copy(update={"tries": current.tries + 1}), version)
        except PersistenceConflictError:
            return False
        version += 1
        given = "".join(ch for ch in to_ascii_digits(code or "") if ch in "0123456789")[:6]
        if not hmac.compare_digest(_hash(current.salt, given), current.code_hash):
            return False
        if consume:
            update = {"tries": current.tries + 1, "used": True}
        else:
            # Kept for the sign-up form, which needs longer than the code's two minutes.
            update = {"tries": current.tries + 1, "expires_at": max(current.expires_at, now + SIGNUP_FORM_TIME)}
        try:
            self._save(current.model_copy(update=update), version)
        except PersistenceConflictError:
            return False
        return True
