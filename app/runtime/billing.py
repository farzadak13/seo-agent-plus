"""Sign-up's code by SMS, and paying for a plan: built from settings, or not at all."""
from __future__ import annotations

from app.accounts.otp import OtpService
from app.billing.plans import catalog, parse_prices
from app.billing.service import Billing
from app.billing.store import PaymentStore, SubscriptionStore
from app.billing.zarinpal import CALLBACK_PATH, ZarinpalGateway
from app.models.sites import SecretProvider, SecretRef
from app.notify.sms import LogSender, SmsIrSender


def _secret(secret_resolver, ref: str) -> str:
    return secret_resolver.resolve(SecretRef(provider=SecretProvider.ENVIRONMENT, key=ref))


def build_otp(config, *, repository, secret_resolver) -> OtpService | None:
    if config.sms_mode == "none":
        return None
    if config.sms_mode == "log":
        sender = LogSender()
    else:
        sender = SmsIrSender(api_key=_secret(secret_resolver, config.sms_api_key_ref), template_id=config.sms_template_id)
    return OtpService(repository=repository, sender=sender)


def build_billing(config, *, repository, secret_resolver) -> Billing:
    """Always built: without a gateway it still knows plans and trials, and says payment is not available."""
    gateway = None
    if config.billing_mode != "none":
        gateway = ZarinpalGateway(
            merchant_id=_secret(secret_resolver, config.zarinpal_merchant_ref),
            sandbox=config.billing_mode == "zarinpal_sandbox",
        )
    callback = f"{config.public_base_url.rstrip('/')}{CALLBACK_PATH}" if config.public_base_url else None
    return Billing(
        subscriptions=SubscriptionStore(repository),
        payments=PaymentStore(repository),
        plans=catalog(parse_prices(config.plan_prices)),
        gateway=gateway,
        callback_url=callback,
        transaction_factory=repository.transaction,
    )
