import pytest

from app.models.provider_config import (
    ArvanAIProviderConfig,
)


def test_arvan_config_defaults():
    config = ArvanAIProviderConfig(
        endpoint="https://example.com",
        api_key="test-key",
        model="test-model",
    )

    assert config.max_tokens == 3000
    assert config.temperature == 0.7
    assert config.max_retries == 2
    assert config.timeout_seconds == 60.0


def test_arvan_config_accepts_custom_values():
    config = ArvanAIProviderConfig(
        endpoint="https://example.com/custom",
        api_key="test-key",
        model="DeepSeek-R1-qwen-7b-awq",
        max_tokens=1000,
        temperature=0.2,
        max_retries=4,
        timeout_seconds=30,
    )

    assert config.endpoint == (
        "https://example.com/custom"
    )
    assert config.model == (
        "DeepSeek-R1-qwen-7b-awq"
    )
    assert config.max_tokens == 1000
    assert config.temperature == 0.2
    assert config.max_retries == 4
    assert config.timeout_seconds == 30


def test_empty_api_key_is_rejected():
    with pytest.raises(ValueError):
        ArvanAIProviderConfig(
            endpoint="https://example.com",
            api_key="",
            model="test-model",
        )


def test_empty_endpoint_is_rejected():
    with pytest.raises(ValueError):
        ArvanAIProviderConfig(
            endpoint="",
            api_key="test-key",
            model="test-model",
        )


def test_zero_retries_are_allowed():
    config = ArvanAIProviderConfig(
        endpoint="https://example.com",
        api_key="test-key",
        model="test-model",
        max_retries=0,
    )

    assert config.max_retries == 0