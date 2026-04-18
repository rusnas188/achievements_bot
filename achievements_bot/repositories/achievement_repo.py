from sqlalchemy.orm import Session
from achievements_bot.db.models import Achievement, User
from typing import Optional, List

class AchievementRepo:
    def __init__(self, db: Session):
        self.db = db

    # def create(self, code: str, title: str, description: str, points: int, creator: Optional[User]) -> Achievement:
    #     ach = Achievement(
    #         code=code,
    #         title=title,
    #         description=description,
    #         points=points,
    #         created_by_user_id=creator.id if creator else None
    #     )
    #     self.db.add(ach)
    #     self.db.commit()
    #     self.db.refresh(ach)
    #     return ach

    def get_by_id(self, id: int) -> Optional[Achievement]:
        return self.db.query(Achievement).filter_by(id=id).first()

    def list_all(self) -> List[Achievement]:
        return self.db.query(Achievement).all()
