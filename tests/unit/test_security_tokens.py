import pytest

from agentic_interviewer.security import Principal, TokenService
from agentic_interviewer.security.tokens import AuthenticationError


def test_signed_token_round_trip_and_tamper_rejection():
    service = TokenService("a-secret-that-is-definitely-longer-than-32-characters")
    principal = Principal("user-1", "tenant-a", frozenset({"candidate"}))
    token = service.issue(principal)
    assert service.verify(token) == principal

    replacement = "a" if token[-1] != "a" else "b"
    with pytest.raises(AuthenticationError, match="signature"):
        service.verify(token[:-1] + replacement)

    with pytest.raises(AuthenticationError, match="malformed"):
        service.verify("not.valid")
