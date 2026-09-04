from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str]


class TokenService:
    """Small HS256 session-token boundary; replaceable by an OIDC adapter in production."""

    def __init__(self, secret: str, *, issuer: str = "agentic-interviewer") -> None:
        if len(secret) < 32:
            raise ValueError("token secret must contain at least 32 characters")
        self._secret = secret.encode()
        self._issuer = issuer

    def issue(self, principal: Principal, *, ttl_seconds: int = 900) -> str:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        header = self._encode({"alg": "HS256", "typ": "JWT"})
        payload = self._encode(
            {
                "sub": principal.subject,
                "tenant_id": principal.tenant_id,
                "roles": sorted(principal.roles),
                "iss": self._issuer,
                "exp": int(time.time()) + ttl_seconds,
            }
        )
        signing_input = f"{header}.{payload}"
        signature = self._signature(signing_input)
        return f"{signing_input}.{signature}"

    def verify(self, token: str) -> Principal:
        try:
            header, payload, signature = token.split(".")
            signing_input = f"{header}.{payload}"
            if not hmac.compare_digest(signature, self._signature(signing_input)):
                raise AuthenticationError("invalid token signature")
            decoded_header = self._decode(header)
            claims = self._decode(payload)
            if decoded_header != {"alg": "HS256", "typ": "JWT"}:
                raise AuthenticationError("unsupported token header")
            if claims.get("iss") != self._issuer:
                raise AuthenticationError("invalid token issuer")
            if int(claims["exp"]) <= int(time.time()):
                raise AuthenticationError("token expired")
            return Principal(
                subject=str(claims["sub"]),
                tenant_id=str(claims["tenant_id"]),
                roles=frozenset(str(role) for role in claims["roles"]),
            )
        except AuthenticationError:
            raise
        except (
            binascii.Error,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            raise AuthenticationError("malformed token") from exc

    def _signature(self, signing_input: str) -> str:
        digest = hmac.new(self._secret, signing_input.encode(), hashlib.sha256).digest()
        return self._b64(digest)

    @classmethod
    def _encode(cls, value: dict) -> str:
        return cls._b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())

    @staticmethod
    def _decode(value: str) -> dict:
        padding = "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(value + padding))

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
