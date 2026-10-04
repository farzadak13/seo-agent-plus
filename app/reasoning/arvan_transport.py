import json
from urllib import error, request

from app.models.llm import (
    LLMFailureType,
    LLMMessage,
)
from app.models.provider_config import (
    ArvanAIProviderConfig,
)


class ArvanTransportError(RuntimeError):
    def __init__(
        self,
        *,
        failure_type: LLMFailureType,
        message: str,
        retryable: bool,
    ) -> None:
        self.failure_type = failure_type
        self.retryable = retryable

        super().__init__(message)


class ArvanTransport:
    def __init__(
        self,
        *,
        config: ArvanAIProviderConfig,
    ) -> None:
        self._config = config

        endpoint = config.endpoint.rstrip("/")

        if endpoint.endswith(
            "/chat/completions"
        ):
            self._url = endpoint
        else:
            self._url = (
                f"{endpoint}/chat/completions"
            )

        self._last_usage: dict = {}
        self._last_response_metadata: dict = {}

    @property
    def provider_id(self) -> str:
        return "arvan_aiaas"

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def last_usage(self) -> dict:
        return dict(self._last_usage)

    @property
    def last_response_metadata(self) -> dict:
        return dict(
            self._last_response_metadata
        )

    def complete(
        self,
        *,
        messages: list[LLMMessage],
    ) -> str:
        self._last_usage = {}
        self._last_response_metadata = {}

        payload = {
            "model": self._config.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }

        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        http_request = request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": (
                    f"apikey {self._config.api_key}"
                ),
                "Content-Type": (
                    "application/json"
                ),
            },
        )

        try:
            with request.urlopen(
                http_request,
                timeout=self._config.timeout_seconds,
            ) as response:
                raw_body = response.read()

        except error.HTTPError as exc:
            # The status alone does not say whether the key, the model name or
            # the quota is wrong; the body does. Keep a bounded piece of it.
            detail = _error_detail(exc)
            if exc.code == 401:
                raise ArvanTransportError(
                    failure_type=(
                        LLMFailureType.AUTHENTICATION
                    ),
                    message=(
                        "Arvan AIaaS authentication failed."
                        f"{detail}"
                    ),
                    retryable=False,
                ) from exc

            if exc.code == 429:
                raise ArvanTransportError(
                    failure_type=(
                        LLMFailureType.RATE_LIMIT
                    ),
                    message=(
                        "Arvan AIaaS rate limit exceeded."
                        f"{detail}"
                    ),
                    retryable=True,
                ) from exc

            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.TRANSPORT
                ),
                message=(
                    f"Arvan AIaaS HTTP error: "
                    f"{exc.code}{detail}"
                ),
                retryable=exc.code >= 500,
            ) from exc

        except TimeoutError as exc:
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.TIMEOUT
                ),
                message=(
                    "Arvan AIaaS request timed out."
                ),
                retryable=True,
            ) from exc

        except error.URLError as exc:
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.TRANSPORT
                ),
                message=(
                    "Arvan AIaaS network request failed: "
                    f"{exc.reason}"
                ),
                retryable=True,
            ) from exc

        except OSError as exc:
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.TRANSPORT
                ),
                message=(
                    "Arvan AIaaS transport failed."
                ),
                retryable=True,
            ) from exc

        try:
            response_payload = json.loads(
                raw_body.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS returned invalid JSON."
                ),
                retryable=False,
            ) from exc

        if not isinstance(
            response_payload,
            dict,
        ):
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS response root "
                    "must be a JSON object."
                ),
                retryable=False,
            )

        usage = response_payload.get(
            "usage"
        )

        if isinstance(
            usage,
            dict,
        ):
            self._last_usage = dict(usage)
        else:
            self._last_usage = {}

        choices = response_payload.get(
            "choices"
        )

        if not isinstance(
            choices,
            list,
        ) or not choices:
            self._last_response_metadata = {
                "id": response_payload.get("id"),
                "model": response_payload.get("model"),
                "object": response_payload.get("object"),
                "finish_reason": None,
            }

            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS response "
                    "has no choices."
                ),
                retryable=False,
            )

        first_choice = choices[0]

        if not isinstance(
            first_choice,
            dict,
        ):
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS choice is invalid."
                ),
                retryable=False,
            )

        finish_reason = first_choice.get(
            "finish_reason"
        )

        self._last_response_metadata = {
            "id": response_payload.get("id"),
            "model": response_payload.get("model"),
            "object": response_payload.get("object"),
            "finish_reason": finish_reason,
        }

        message = first_choice.get(
            "message"
        )

        if not isinstance(
            message,
            dict,
        ):
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS response "
                    "has no message."
                ),
                retryable=False,
            )

        content = message.get(
            "content"
        )

        if not isinstance(
            content,
            str,
        ):
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS message content "
                    "is missing or invalid."
                ),
                retryable=False,
            )

        if not content.strip():
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.EMPTY_OUTPUT
                ),
                message=(
                    "Arvan AIaaS returned empty content."
                ),
                retryable=False,
            )

        return content


def _error_detail(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    if not body:
        return ""
    return f" Response: {body[:300]}"
