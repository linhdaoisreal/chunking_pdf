import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str = Field(max_length=254)
    password: SecretStr = Field(min_length=6, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        # Product addresses use an ASCII dot-atom local part and a DNS domain.
        local, separator, domain = value.partition("@")
        if (
            not separator
            or len(local) > 64
            or not re.fullmatch(
                r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*", local
            )
            or "." not in domain
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")
            )
        ):
            raise ValueError("Invalid email address")
        return value


class RegisterRequest(LoginRequest):
    display_name: str | None = Field(default=None, max_length=200)
