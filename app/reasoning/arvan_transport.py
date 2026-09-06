import json
from urllib import error, request

from app.models.llm import (
    LLMMessage,
    LLMFailureType,
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

    @property
    def provider_id(self) -> str:
        return "arvan_aiaas"

    @property
    def model(self) -> str:
        return self._config.model

    def complete(
        self,
        *,
        messages: list[LLMMessage],
    ) -> str:
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
            if exc.code == 401:
                raise ArvanTransportError(
                    failure_type=(
                        LLMFailureType.AUTHENTICATION
                    ),
                    message=(
                        "Arvan AIaaS authentication failed."
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
                    ),
                    retryable=True,
                ) from exc

            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.TRANSPORT
                ),
                message=(
                    f"Arvan AIaaS HTTP error: {exc.code}"
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
                    "Arvan AIaaS network request failed."
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

        choices = response_payload.get(
            "choices"
        )

        if not isinstance(
            choices,
            list,
        ) or not choices:
            raise ArvanTransportError(
                failure_type=(
                    LLMFailureType.INVALID_JSON
                ),
                message=(
                    "Arvan AIaaS response has no choices."
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

        return content