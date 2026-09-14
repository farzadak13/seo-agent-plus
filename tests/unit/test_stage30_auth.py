import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.auth import APIKeyAuthenticator


@pytest.fixture
def auth():
    return APIKeyAuthenticator("secret-123", principal_id="principal-1")


def test_valid_bearer_is_accepted(auth):
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="secret-123",
    )

    assert auth.authenticate(credentials) == "principal-1"


def test_wrong_key_is_rejected(auth):
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="wrong",
    )

    with pytest.raises(HTTPException) as exc:
        auth.authenticate(credentials)

    assert exc.value.status_code == 401


def test_missing_credentials_are_rejected(auth):
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(None)

    assert exc.value.status_code == 401


def test_non_bearer_scheme_is_rejected(auth):
    credentials = HTTPAuthorizationCredentials(
        scheme="Basic",
        credentials="secret-123",
    )

    with pytest.raises(HTTPException) as exc:
        auth.authenticate(credentials)

    assert exc.value.status_code == 401


def test_empty_key_is_rejected():
    with pytest.raises(ValueError):
        APIKeyAuthenticator("")
