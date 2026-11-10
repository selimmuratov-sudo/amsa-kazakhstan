from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import current_user


def authenticated_user(request: Request, db: Session = Depends(get_db)) -> Generator[User, None, None]:
    yield current_user(request, db)
