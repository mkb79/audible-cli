from fastapi import APIRouter, Depends

from .. import models, schemas
from ..deps import get_current_user

router = APIRouter()


@router.get("/api/me", response_model=schemas.Me)
async def me(user: models.User = Depends(get_current_user)) -> models.User:
    return user
