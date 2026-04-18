from sqlalchemy.orm import Session
from sqlalchemy import desc

from achievements_bot.repositories.user_repo import UserRepo
from achievements_bot.repositories.achievement_repo import AchievementRepo
from achievements_bot.repositories.user_achievement_repo import UserAchievementRepo

from achievements_bot.db.models import (
    User,
    Achievement,
    UserAchievement,
    Season,
    SeasonUserStat
)

from typing import List, Tuple, Optional


class AuthError(Exception):
    pass


class AlreadyHasAchievement(Exception):
    pass


class NotFoundError(Exception):
    pass


class AchievementService:
    def __init__(self, db: Session):
        self.db = db
        self.users = UserRepo(db)
        self.achs = AchievementRepo(db)
        self.ua = UserAchievementRepo(db)

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def get_active_season(self) -> Season:
        season = self.db.query(Season).filter_by(is_active=True).first()

        if not season:
            raise Exception("Активный сезон не найден")

        return season

    def ensure_season_stat_exists(self, season_id: int, user_id: int) -> SeasonUserStat:
        stat = self.db.query(SeasonUserStat).filter_by(
            season_id=season_id,
            user_id=user_id
        ).first()

        if not stat:
            stat = SeasonUserStat(
                season_id=season_id,
                user_id=user_id,
                achievement_points=0,
                attendance_points=0
            )
            self.db.add(stat)
            self.db.flush()

        return stat

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------

    def register_user(self, tg_id: int, full_name: str, username: Optional[str] = None) -> User:
        user = self.users.get_or_create_by_tg(tg_id, username=username)
        return self.users.set_full_name(user, full_name)

    def list_achievements(self) -> List[Achievement]:
        return self.achs.list_all()

    def list_user_achievements(self, tg_id: int) -> Tuple[User, List[UserAchievement]]:
        season = self.get_active_season()

        user = self.users.get_by_tg(tg_id)
        if not user:
            raise NotFoundError("Пользователь не найден")

        items = self.ua.list_for_user(user, season.id)

        return user, items

    def list_user_achievements_for_season(self, user_id: int, season_id: int):
        return self.db.query(UserAchievement).filter_by(
            user_id=user_id,
            season_id=season_id
        ).all()

    def get_leaderboard(self):
        season = self.get_active_season()

        return (
            self.db.query(SeasonUserStat)
            .filter_by(season_id=season.id)
            .order_by(
                desc(
                    SeasonUserStat.achievement_points +
                    SeasonUserStat.attendance_points
                )
            )
            .all()
        )

    # -------------------------------------------------
    # Admin security
    # -------------------------------------------------

    def require_admin(self, tg_id: int) -> User:
        user = self.users.get_by_tg(tg_id)

        if not user or not user.is_admin:
            raise AuthError("Требуются права администратора")

        return user

    # -------------------------------------------------
    # Achievement control
    # -------------------------------------------------

    def delete_achievement(self, admin_id: int, id: int) -> bool:
        admin = self.require_admin(admin_id)

        achievement = self.db.query(Achievement).filter_by(id=id).first()

        if not achievement:
            return False

        self.db.query(UserAchievement).filter_by(
            achievement_id=achievement.id
        ).delete()

        self.db.delete(achievement)
        self.db.commit()

        return True

    # -------------------------------------------------
    # Grant achievement
    # -------------------------------------------------

    def grant_achievement(self,
                          admin_tg_id: int,
                          target_tg_id: int,
                          achievement_id: int):

        admin = self.require_admin(admin_tg_id)

        user = self.users.get_by_tg(target_tg_id)
        if not user:
            raise NotFoundError("Пользователь не найден")

        ach = self.achs.get_by_id(achievement_id)
        if not ach:
            raise NotFoundError("Ачивка не найдена")

        season = self.get_active_season()

        existing = self.db.query(UserAchievement).filter_by(
            user_id=user.id,
            achievement_id=ach.id,
            season_id=season.id,
        ).first()

        if existing:
            raise AlreadyHasAchievement(
                "У пользователя уже есть эта ачивка в этом сезоне"
            )

        # Create user achievement record
        ua = self.ua.grant(
            user=user,
            achievement=ach,
            awarded_by=admin,
            season_id=season.id,
        )

        # Update stats safely
        stat = self.ensure_season_stat_exists(
            season.id,
            user.id
        )

        stat.achievement_points += ach.points

        self.db.commit()

        return ua

    # -------------------------------------------------
    # Revoke achievement
    # -------------------------------------------------

    def revoke_achievement(self,
                           admin_tg_id: int,
                           target_tg_id: int,
                           achievement_id: int):

        self.require_admin(admin_tg_id)

        user = self.users.get_by_tg(target_tg_id)
        if not user:
            raise NotFoundError("Пользователь не найден")

        ach = self.achs.get_by_id(achievement_id)
        if not ach:
            raise NotFoundError("Ачивка не найдена")

        season = self.get_active_season()

        self.ua.revoke(
            user=user,
            achievement=ach,
            season_id=season.id,
        )

        stat = self.ensure_season_stat_exists(
            season.id,
            user.id
        )

        stat.achievement_points -= ach.points

        self.db.commit()