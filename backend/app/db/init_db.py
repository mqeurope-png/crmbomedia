from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import get_engine
from app.models.crm import User, UserRole
from app.repositories.crm import get_user_by_email


def ensure_default_admin() -> None:
    settings = get_settings()
    with Session(get_engine()) as session:
        email = settings.default_admin_email.lower()
        if get_user_by_email(session, email):
            return
        user = User(
            email=email,
            full_name="Default Admin",
            password_hash=hash_password(settings.default_admin_password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(user)
        session.commit()


if __name__ == "__main__":
    ensure_default_admin()
