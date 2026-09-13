"""Session-token authentication shared by every worker endpoint."""

import os
import secrets

from fastapi import Header, HTTPException, status

SESSION_TOKEN_ENV_VAR = "INVOICE_RENAMER_SESSION_TOKEN"


def get_expected_session_token() -> str:
    token = os.environ.get(SESSION_TOKEN_ENV_VAR)
    if not token:
        raise RuntimeError(
            f"{SESSION_TOKEN_ENV_VAR} must be set; the desktop shell generates it at startup."
        )
    return token


async def require_session_token(authorization: str | None = Header(default=None)) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session token"
        )

    provided = authorization.removeprefix("Bearer ")
    expected = get_expected_session_token()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token"
        )
