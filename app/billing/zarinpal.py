"""Zarinpal, the payment gateway: ask for a payment, send the customer there, verify on return.

The amount sent and verified is always ours, read from the payment record,
never from the callback's query string: the return page can be forged, a
server-to-server verify of our own amount cannot. Amounts go in rials
(``currency: IRR`` said outright), prices are kept in tomans.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

# Where the gateway sends the customer back, under the public address.
CALLBACK_PATH = "/v1/billing/zarinpal/callback"
LIVE = "https://payment.zarinpal.com"
SANDBOX = "https://sandbox.zarinpal.com"
TIMEOUT_SECONDS = 20
# 100: paid now. 101: paid, and already verified by an earlier call.
PAID_CODES = frozenset({100, 101})

Transport = Callable[[str, dict], dict]


class GatewayError(RuntimeError):
    """The gateway refused, or could not be reached. The message is for the log, not the customer."""


def default_transport(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https host
            return json.loads(response.read(64_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Zarinpal answers its own refusals as JSON with an error status.
        try:
            return json.loads(exc.read(64_000).decode("utf-8"))
        except (ValueError, OSError):
            raise GatewayError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise GatewayError(type(exc).__name__) from exc


@dataclass(frozen=True)
class Verified:
    paid: bool
    ref_id: str | None = None
    card_pan: str | None = None
    code: int | None = None


def _code(answer: dict) -> int | None:
    data = answer.get("data")
    if isinstance(data, dict) and isinstance(data.get("code"), int):
        return data["code"]
    return None


class ZarinpalGateway:
    def __init__(self, *, merchant_id: str, sandbox: bool = False, transport: Transport = default_transport) -> None:
        if len(merchant_id.strip()) != 36:
            raise ValueError("A Zarinpal merchant id is 36 characters.")
        self._merchant = merchant_id.strip()
        self._base = SANDBOX if sandbox else LIVE
        self._transport = transport

    def request(self, *, amount_toman: int, description: str, callback_url: str, mobile: str | None = None, order_id: str) -> str:
        """The authority for a new payment. Raises GatewayError when the gateway says no."""
        metadata: dict[str, str] = {"order_id": order_id}
        if mobile:
            metadata["mobile"] = mobile
        answer = self._transport(
            f"{self._base}/pg/v4/payment/request.json",
            {
                "merchant_id": self._merchant,
                "amount": amount_toman * 10,
                "currency": "IRR",
                "description": description[:250],
                "callback_url": callback_url,
                "metadata": metadata,
            },
        )
        data = answer.get("data") if isinstance(answer.get("data"), dict) else {}
        if _code(answer) != 100 or not data.get("authority"):
            raise GatewayError(f"request refused: code {_code(answer)}, errors {answer.get('errors')!r}"[:300])
        return str(data["authority"])

    def start_url(self, authority: str) -> str:
        return f"{self._base}/pg/StartPay/{authority}"

    def verify(self, *, amount_toman: int, authority: str) -> Verified:
        answer = self._transport(
            f"{self._base}/pg/v4/payment/verify.json",
            {"merchant_id": self._merchant, "amount": amount_toman * 10, "authority": authority},
        )
        code = _code(answer)
        if code in PAID_CODES:
            data = answer["data"]
            return Verified(paid=True, ref_id=str(data.get("ref_id") or "") or None, card_pan=data.get("card_pan"), code=code)
        return Verified(paid=False, code=code)
