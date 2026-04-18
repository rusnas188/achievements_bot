from sqlalchemy.orm import Session
from achievements_bot.db.models import User, Achievement, UserAchievement
from typing import List, Optional

class UserAchievementRepo:
    def __init__(self, db: Session):
        self.db = db

    def grant(
        self,
        user: User,
        achievement: Achievement,
        awarded_by: Optional[User],
        season_id: int,
    ) -> UserAchievement:

        exists = (
            self.db.query(UserAchievement)
            .filter_by(
                user_id=user.id,
                achievement_id=achievement.id,
                season_id=season_id,
            )
            .first()
        )
        if exists:
            return exists

        ua = UserAchievement(
            user=user,
            achievement=achievement,
            awarded_by=awarded_by,
            season_id=season_id,
        )

        self.db.add(ua)
        self.db.commit()
        self.db.refresh(ua)

        return ua

    def revoke(
        self,
        user: User,
        achievement: Achievement,
        season_id: int,
    ) -> None:

        ua = (
            self.db.query(UserAchievement)
            .filter_by(
                user_id=user.id,
                achievement_id=achievement.id,
                season_id=season_id,
            )
            .first()
        )

        if ua:
            self.db.delete(ua)
            self.db.commit()

    def list_for_user(self, user: User, season_id: int) -> List[UserAchievement]:
        return (
            self.db.query(UserAchievement)
            .filter_by(user_id=user.id, season_id=season_id)
            .all()
        )