from typing import Literal

from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class LogoutResponse(BaseModel):
    # Delete the access token on the client. It remains valid until expiry.
    message: str = "Delete the access token on the client."
