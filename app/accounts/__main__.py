"""The operator's commands for dashboard accounts.

    python -m app.accounts create --tenant t-xxxx --email a@example.com
    python -m app.accounts create --tenant t-xxxx --email a@example.com --google-only
    python -m app.accounts set-password --email a@example.com
    python -m app.accounts disable --email a@example.com
    python -m app.accounts enable --email a@example.com
    python -m app.accounts revoke-sessions --email a@example.com
    python -m app.accounts unlink-google --email a@example.com
    python -m app.accounts allow-google --email a@example.com
    python -m app.accounts list --tenant t-xxxx

A password is typed at a hidden prompt, twice, and never given as an
argument: arguments are visible to every user of the machine through `ps`,
and they stay in shell history. Disabling an account, setting its password,
unlinking its Google identity and revoke-sessions each end every session
opened before, for good: enabling the account again does not bring them back.

unlink-google cuts the account off from Google sign-in entirely: the linked
Google account no longer signs in, and no Google account links itself again
by the account's email until allow-google. A password, if the account has
one, keeps working; to shut a person out completely, use disable.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from app.accounts.models import AccountStatus
from app.accounts.passwords import WeakPasswordError, check_strength, hash_password
from app.accounts.service import AuthService
from app.accounts.store import AccountChangedError, AccountExistsError, AccountStore, SessionStore
from app.persistence.postgres import PostgresRepository
from app.tenancy.store import TenantStore


def _ask_password() -> str:
    first = getpass.getpass("Password (hidden): ")
    try:
        check_strength(first)
    except WeakPasswordError as exc:
        sys.exit(str(exc))
    if getpass.getpass("Again: ") != first:
        sys.exit("The two passwords differ; nothing was changed.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage dashboard accounts.")
    parser.add_argument("--dsn", default=None, help="Defaults to SEO_AGENT_DATABASE_DSN.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an account for a tenant.")
    create.add_argument("--tenant", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--google-only", action="store_true",
                        help="No password: the person signs in with Google using this email.")
    for name in ("set-password", "disable", "enable", "revoke-sessions", "unlink-google", "allow-google"):
        sub.add_parser(name).add_argument("--email", required=True)
    sub.add_parser("list").add_argument("--tenant", required=True)
    args = parser.parse_args(argv)

    dsn = args.dsn or os.getenv("SEO_AGENT_DATABASE_DSN")
    if not dsn:
        print("SEO_AGENT_DATABASE_DSN is required (or pass --dsn).", file=sys.stderr)
        return 2
    repository = PostgresRepository(dsn)
    accounts = AccountStore(repository)
    service = AuthService(accounts=accounts, sessions=SessionStore(repository), tenants=TenantStore(repository))

    if args.command == "create":
        if TenantStore(repository).find(args.tenant) is None:
            print(f"No tenant {args.tenant}. See: tenant.sh list", file=sys.stderr)
            return 1
        password = None if args.google_only else _ask_password()
        try:
            account = service.create_account(tenant_id=args.tenant, email=args.email, password=password)
        except AccountExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        how = "Google only" if args.google_only else "password (and Google, once linked)"
        print(f"created {account.email} ({account.account_id}) for {account.tenant_id}; signs in with {how}")
        return 0

    if args.command == "list":
        for account in accounts.list_for_tenant(args.tenant):
            password = "password" if account.password_hash else "no password"
            if account.google_subject:
                google = "google linked"
            elif account.google_link_allowed:
                google = "google not linked"
            else:
                google = "google blocked (allow-google to undo)"
            print(f"{account.email}  {account.status.value}  {password}  {google}  last login {account.last_login_at}")
        return 0

    account = accounts.by_email(args.email)
    if account is None:
        print(f"No account for {args.email}.", file=sys.stderr)
        return 1
    # The new password is asked for first, and the account read again after:
    # typing takes long enough for a sign-in to have changed it meanwhile.
    new_hash = hash_password(_ask_password()) if args.command == "set-password" else None
    account = accounts.by_email(args.email)
    try:
        if args.command == "set-password":
            service.end_sessions(account, password_hash=new_hash)
            print("password set; every earlier session has ended")
        elif args.command == "unlink-google":
            service.unlink_google(account)
            print(f"Google sign-in is now off for {account.email} (allow-google to undo); every session has ended")
        elif args.command == "allow-google":
            service.allow_google(account)
            print(f"a Google account with the email {account.email} may now link itself on its next sign-in")
        elif args.command == "revoke-sessions":
            service.end_sessions(account)
            print(f"every session of {account.email} has ended")
        elif args.command == "disable":
            service.end_sessions(account, status=AccountStatus.DISABLED)
            print(f"{account.email} is now disabled; every session has ended")
        else:
            accounts.update(account.model_copy(update={"status": AccountStatus.ACTIVE}))
            print(f"{account.email} is now active (earlier sessions stay ended)")
    except AccountChangedError:
        print("The account changed while this ran (someone signed in, say). Run it again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
