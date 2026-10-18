"""Authenticated workspace integration settings."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import Settings
from app.core.security import TokenCipher
from app.db.models import User
from app.db.session import get_session
from app.schemas.settings import GitHubTokenRequest, GitHubTokenResponse

router = APIRouter(prefix="/settings", tags=["settings"])


@router.put("/github-token", response_model=GitHubTokenResponse)
async def save_github_token(
    payload: GitHubTokenRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GitHubTokenResponse:
    """Encrypt and update only the owner derived from the verified session."""
    settings = cast(Settings, request.app.state.settings)
    user.encrypted_github_pat = TokenCipher(settings.encryption_secret_key).encrypt(
        payload.token
    )
    await session.flush()
    return GitHubTokenResponse()
