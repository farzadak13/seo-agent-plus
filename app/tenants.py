"""Tenant administration from the command line.

    python -m app.tenants list
    python -m app.tenants create --name "PAMA"
    python -m app.tenants issue-key --tenant t-xxxx --label "server"
    python -m app.tenants revoke-key --key-id <key_id>
    python -m app.tenants suspend --tenant t-xxxx
    python -m app.tenants activate --tenant t-xxxx

Creating a customer is a rare, deliberate act. Doing it from a command line
rather than an HTTP endpoint means there is no way to create a tenant over the
network at all, which is one fewer thing to get wrong.

A key's plaintext is printed once, when it is issued, and is not recoverable
afterwards: only its hash is stored. Losing it means issuing another and
revoking the first.
"""
from __future__ import annotations

import argparse
import os
import sys
from uuid import uuid4

from app.models.tenants import APIKeyRecord, Tenant, TenantStatus
from app.persistence.postgres import PostgresRepository
from app.tenancy import keys as key_format
from app.tenancy.store import APIKeyStore, TenantStore


def _stores(dsn: str):
    repository = PostgresRepository(dsn)
    return TenantStore(repository), APIKeyStore(repository)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage tenants and their API keys.")
    parser.add_argument("--dsn", default=None, help="Defaults to SEO_AGENT_DATABASE_DSN.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List tenants and their keys.")

    create = sub.add_parser("create", help="Create a tenant and issue its first key.")
    create.add_argument("--name", required=True)
    create.add_argument("--no-key", action="store_true", help="Create the tenant only.")

    issue = sub.add_parser("issue-key", help="Issue another key for a tenant.")
    issue.add_argument("--tenant", required=True)
    issue.add_argument("--label", default="")

    revoke = sub.add_parser("revoke-key", help="Revoke a key by its public id.")
    revoke.add_argument("--key-id", required=True)

    for name in ("suspend", "activate"):
        action = sub.add_parser(name, help=f"{name.capitalize()} a tenant.")
        action.add_argument("--tenant", required=True)

    args = parser.parse_args(argv)

    dsn = args.dsn or os.getenv("SEO_AGENT_DATABASE_DSN")
    if not dsn or not dsn.strip():
        print("SEO_AGENT_DATABASE_DSN is required (or pass --dsn).", file=sys.stderr)
        return 2

    tenants, api_keys = _stores(dsn)

    if args.command == "list":
        found = tenants.list_all()
        if not found:
            print("No tenants yet.")
            return 0
        for tenant in sorted(found, key=lambda item: item.created_at):
            print(f"{tenant.tenant_id}  {tenant.status:9}  {tenant.name}")
            for key in api_keys.list_for_tenant(tenant.tenant_id):
                state = "active " if key.active else "revoked"
                label = f"  {key.label}" if key.label else ""
                print(f"    {key.key_id}  {state}{label}")
        return 0

    if args.command == "create":
        tenant = tenants.create(
            Tenant(tenant_id=f"t-{uuid4().hex[:16]}", name=args.name)
        )
        print(f"tenant: {tenant.tenant_id}  {tenant.name}")
        if args.no_key:
            return 0
        _issue(api_keys, tenant.tenant_id, "initial")
        return 0

    if args.command == "issue-key":
        if tenants.find(args.tenant) is None:
            print(f"No such tenant: {args.tenant}", file=sys.stderr)
            return 1
        _issue(api_keys, args.tenant, args.label)
        return 0

    if args.command == "revoke-key":
        record = api_keys.find(args.key_id)
        if record is None:
            print(f"No such key: {args.key_id}", file=sys.stderr)
            return 1
        revoked = api_keys.revoke(args.key_id)
        print(f"revoked {revoked.key_id} (tenant {revoked.tenant_id})")
        print("It stops working on the next request; nothing is cached.")
        return 0

    if args.command in {"suspend", "activate"}:
        tenant = tenants.find(args.tenant)
        if tenant is None:
            print(f"No such tenant: {args.tenant}", file=sys.stderr)
            return 1
        status = TenantStatus.SUSPENDED if args.command == "suspend" else TenantStatus.ACTIVE
        updated = tenants.update(tenant.model_copy(update={"status": status}))
        print(f"{updated.tenant_id} is now {updated.status}")
        return 0

    return 2


def _issue(api_keys: APIKeyStore, tenant_id: str, label: str) -> None:
    issued = key_format.issue()
    api_keys.create(
        APIKeyRecord(
            key_id=issued.key_id,
            tenant_id=tenant_id,
            digest=issued.digest,
            label=label,
        )
    )
    print()
    print("  key id:  " + issued.key_id + "   (public; safe in logs and tickets)")
    print("  key:     " + issued.token)
    print()
    print("  Store it now. Only its hash is kept, so this is the only time it")
    print("  is shown. If it is lost, issue another and revoke this one.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
