"""Sending a sign-in code by SMS.

SMS.ir's "verify" send: a template approved in the panel, with the code as its
one parameter, delivered on the fast lane meant for codes. The key goes in a
header, never in the address, so it does not end up in anyone's access log.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

SMSIR_VERIFY_URL = "https://api.sms.ir/v1/send/verify"
TIMEOUT_SECONDS = 15


class SmsError(RuntimeError):
    """The code was not sent. The message is for the log, not the customer."""


class SmsSender(Protocol):
    def send_code(self, *, mobile: str, code: str) -> None: ...


Transport = Callable[[str, dict, dict], tuple[int, dict]]


def default_transport(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https host
            return response.status, json.loads(response.read(16_000).decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read(16_000).decode("utf-8") or "{}")
        except (ValueError, OSError):
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise SmsError(type(exc).__name__) from exc


class SmsIrSender:
    def __init__(self, *, api_key: str, template_id: int, parameter: str = "CODE", transport: Transport = default_transport) -> None:
        self._key = api_key
        self._template = template_id
        self._parameter = parameter
        self._transport = transport

    def send_code(self, *, mobile: str, code: str) -> None:
        status, answer = self._transport(
            SMSIR_VERIFY_URL,
            {"Content-Type": "application/json", "Accept": "application/json", "X-API-KEY": self._key},
            {"mobile": mobile, "templateId": self._template, "parameters": [{"name": self._parameter, "value": code}]},
        )
        # status 1 is SMS.ir's "sent"; anything else is a refusal with a reason.
        if status != 200 or answer.get("status") != 1:
            raise SmsError(f"SMS.ir refused: HTTP {status}, status {answer.get('status')!r}")


class LogSender:
    """For a developer's machine only: the code goes to the log instead of a phone."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_code(self, *, mobile: str, code: str) -> None:
        self.sent.append((mobile, code))
        log.warning("development SMS to %s: %s", mobile, code)
