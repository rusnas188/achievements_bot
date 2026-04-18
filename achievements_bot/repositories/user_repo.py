from sqlalchemy.orm import Session
from achievements_bot.db.models import User
from typing import Optional, List
from achievements_bot.config import get_settings

class UserRepo:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_by_tg(self, tg_id: int, username: Optional[str] = None) -> User:
        user = self.db.query(User).filter_by(tg_id=tg_id).first()
        if not user:
            user = User(tg_id=tg_id, username=username)

            if tg_id == get_settings().admin_tg_id:
                user.is_admin = True

            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
        else:
            if tg_id == get_settings().admin_tg_id:
                user.is_admin = True

            if username and user.username != username:
                user.username = username
                self.db.commit()
                self.db.refresh(user)
        return user

    def set_full_name(self, user: User, full_name: str) -> User:
        user.full_name = full_name
        self.db.commit()
        self.db.refresh(user)
        return user

    # def set_username(self, user: User, username: str) -> User:
    #     user.username = username
    #     self.db.commit()
    #     self.db.refresh(user)
    #     return user

    def get_by_tg(self, tg_id: int) -> Optional[User]:
        return self.db.query(User).filter_by(tg_id=tg_id).first()

    def list_top(self) -> List[User]:
        return (
            self.db.query(User)
            .filter(User.is_admin == False)
            .order_by(User.points.desc())
            .all()
        )


    # def add_points(self, user: User, delta: int) -> None:
    #     user.points += delta
    #     self.db.commit()

    # def set_admin(self, user: User, is_admin: bool) -> None:
    #     user.is_admin = is_admin
    #     self.db.commit()
