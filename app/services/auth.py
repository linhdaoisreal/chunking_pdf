from datetime import UTC, datetime
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: UUID, settings: Settings) -> str:
    now = int(datetime.now(UTC).timestamp())
    assert settings.jwt_secret is not None
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + settings.jwt_access_ttl_seconds,
            "token_type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> UUID:
    assert settings.jwt_secret is not None
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={
            "require": ["sub", "iss", "aud", "iat", "nbf", "exp", "token_type"],
            "strict_aud": True,
        },
    )
    if (
        jwt.get_unverified_header(token).get("typ") != "JWT"
        or claims["token_type"] != "access"
        or any(type(claims[name]) is not int for name in ("iat", "nbf", "exp"))
        or not claims["iat"] <= claims["nbf"] < claims["exp"]
        or not isinstance(claims["sub"], str)
    ):
        raise jwt.InvalidTokenError("Invalid access token claims")
    try:
        return UUID(claims["sub"])
    except ValueError:
        raise jwt.InvalidTokenError("Invalid subject") from None
