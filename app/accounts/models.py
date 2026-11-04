"""People who sign in to the dashboard, and their sessions.

An account belongs to one tenant: signing in is acting as that tenant, with
the same ownership checks an API key gets. An account is created by the
operator (email, password, Google) or by the person signing up with their
mobile number, proved by a code sent to it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    # Lower-cased and trimmed; one of the keys accounts are found by. None for
    # an account that signed up with its mobile number.
    email: str | None = Field(default=None, min_length=3, max_length=254)
    # 09XXXXXXXXX (app.accounts.phone); the other key.
    mobile: str | None = Field(default=None, pattern=r"^09[0-9]{9}$")
    terms_accepted_at: datetime | None = None
    status: AccountStatus = AccountStatus.ACTIVE
    # "scrypt$..." or None for an account that signs in with Google only.
    password_hash: str | None = Field(default=None, repr=False)
    # Google's stable id for the person, bound on their first Google sign-in.
    google_subject: str | None = None
    # Whether a Google sign-in with this account's (verified) email may link
    # itself. Unlinking turns it off: otherwise the very Google account the
    # operator just cut off would link itself again on its next sign-in.
    # Only the operator turns it back on (account.sh allow-google).
    google_link_allowed: bool = True
    # Every session carries the generation it was opened under and counts
    # only while the account still has it. Disabling the account, changing its
    # password, unlinking Google or revoking sessions increments it, in the same
    # write, so "disable, change password, enable" really shuts a thief out.
    # A counter, not a time: a sign-in racing the operator's write either
    # sees the new generation and is refused, or opened its session under
    # the old one, which stops counting as soon as that write lands.
    session_generation: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_login_at: datetime | None = None

    @model_validator(mode="after")
    def found_by_something(self):
        if self.email is None and self.mobile is None:
            raise ValueError("An account needs an email or a mobile number.")
        return self


class LoginMethod(StrEnum):
    PASSWORD = "password"
    GOOGLE = "google"
    SMS = "sms"


class Session(BaseModel):
    """A signed-in browser. The token itself is never stored, only its hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token_hash: str = Field(min_length=64, max_length=64)
    account_id: str
    tenant_id: str
    method: LoginMethod
    generation: int = Field(default=0, ge=0)
    # Sent back on every state-changing request, so another site cannot make
    # the browser act on the user's behalf with their cookie.
    csrf_token: str = Field(repr=False)
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None
