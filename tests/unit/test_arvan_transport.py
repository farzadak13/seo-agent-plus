import json

import pytest

from app.models.llm import (
    LLMFailureType,
    LLMMessage,
)
from app.models.provider_config import (
    ArvanAIProviderConfig,
)
from app.reasoning.arvan_transport import (
    ArvanTransport,
    ArvanTransportError,
)


def make_config() -> ArvanAIProviderConfig:
    return ArvanAIProviderConfig(
        endpoint="https://aiaas.example.com/endpoint",
        api_key="test-api-key",
        model="test-model",
        max_tokens=1500,
        temperature=0.4,
        timeout_seconds=10,
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    def read(self):
        return json.dumps(
            self.payload,
            ensure_ascii=False,
        ).encode("utf-8")


def test_provider_id():
    transport = ArvanTransport(
        config=make_config()
    )

    assert transport.provider_id == (
        "arvan_aiaas"
    )


def test_endpoint_appends_chat_completions():
    transport = ArvanTransport(
        config=make_config()
    )

    assert transport._url == (
        "https://aiaas.example.com/endpoint/"
        "chat/completions"
    )


def test_endpoint_does_not_duplicate_chat_completions():
    config = make_config()

    config.endpoint = (
        "https://aiaas.example.com/endpoint/"
        "chat/completions"
    )

    transport = ArvanTransport(
        config=config
    )

    assert transport._url == (
        "https://aiaas.example.com/endpoint/"
        "chat/completions"
    )


def test_transport_builds_correct_request(
    monkeypatch,
):
    captured = {}

    def fake_urlopen(
        http_request,
        timeout,
    ):
        captured["url"] = (
            http_request.full_url
        )

        captured["headers"] = {
            key.lower(): value
            for key, value
            in http_request.header_items()
        }

        captured["body"] = json.loads(
            http_request.data.decode("utf-8")
        )

        captured["timeout"] = timeout

        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"candidates":[]}'
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        fake_urlopen,
    )

    transport = ArvanTransport(
        config=make_config()
    )

    result = transport.complete(
        messages=[
            LLMMessage(
                role="system",
                content="system",
            ),
            LLMMessage(
                role="user",
                content="user",
            ),
        ]
    )

    assert result == '{"candidates":[]}'

    assert captured["url"] == (
        "https://aiaas.example.com/endpoint/"
        "chat/completions"
    )

    assert (
        captured["headers"]["authorization"]
        == "apikey test-api-key"
    )

    assert (
        captured["headers"]["content-type"]
        == "application/json"
    )

    assert captured["body"] == {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": "system",
            },
            {
                "role": "user",
                "content": "user",
            },
        ],
        "max_tokens": 1500,
        "temperature": 0.4,
    }

    assert captured["timeout"] == 10


def test_401_is_authentication_failure(
    monkeypatch,
):
    from urllib.error import HTTPError

    def fake_urlopen(
        http_request,
        timeout,
    ):
        raise HTTPError(
            url=http_request.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        fake_urlopen,
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.AUTHENTICATION
    )

    assert exc_info.value.retryable is False


def test_429_is_retryable_rate_limit(
    monkeypatch,
):
    from urllib.error import HTTPError

    def fake_urlopen(
        http_request,
        timeout,
    ):
        raise HTTPError(
            url=http_request.full_url,
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        fake_urlopen,
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.RATE_LIMIT
    )

    assert exc_info.value.retryable is True


def test_500_is_retryable(
    monkeypatch,
):
    from urllib.error import HTTPError

    def fake_urlopen(
        http_request,
        timeout,
    ):
        raise HTTPError(
            url=http_request.full_url,
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        fake_urlopen,
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.TRANSPORT
    )

    assert exc_info.value.retryable is True


def test_invalid_response_json_is_not_retryable(
    monkeypatch,
):
    class InvalidResponse(FakeResponse):
        def read(self):
            return b"not-json"

    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        lambda *args, **kwargs: (
            InvalidResponse({})
        ),
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.INVALID_JSON
    )

    assert exc_info.value.retryable is False


def test_missing_choices_is_invalid_response(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        lambda *args, **kwargs: (
            FakeResponse({})
        ),
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.INVALID_JSON
    )


def test_empty_content_is_invalid_response(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.reasoning.arvan_transport.request.urlopen",
        lambda *args, **kwargs: (
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None
                            }
                        }
                    ]
                }
            )
        ),
    )

    transport = ArvanTransport(
        config=make_config()
    )

    with pytest.raises(
        ArvanTransportError
    ) as exc_info:
        transport.complete(messages=[])

    assert (
        exc_info.value.failure_type
        == LLMFailureType.INVALID_JSON
    )