"""Ownership checks shared by repository-scoped API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.errors import ApiError
from app.db.models import Repository, User
from app.db.session import get_session


async def get_owned_repository(
    repo_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Repository:
    """Use a tenant-scoped query and the same denial for missing/foreign IDs."""
    repository = await session.scalar(
        select(Repository).where(
            Repository.id == repo_id, Repository.user_id == user.id
        )
    )
    if repository is None:
        raise ApiError(
            403, "RESOURCE_ACCESS_DENIED", "Repository not found or access denied."
        )
    return repository
