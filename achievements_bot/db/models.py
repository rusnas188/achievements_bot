from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, text, Date
from sqlalchemy.orm import relationship
from datetime import datetime, date
from achievements_bot.db.base import Base


class Season(Base):
    __tablename__ = "seasons"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)

    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    stats = relationship(
        "SeasonUserStat",
        back_populates="season",
        cascade="all, delete-orphan"
    )


class SeasonUserStat(Base):
    __tablename__ = "season_user_stats"

    id = Column(Integer, primary_key=True)

    season_id = Column(
        Integer,
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    achievement_points = Column(Integer, default=0, nullable=False)
    attendance_points = Column(Integer, default=0, nullable=False)

    season = relationship("Season", back_populates="stats")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("season_id", "user_id", name="uq_season_user_once"),
    )
    
    user = relationship(
        "User",
        back_populates="season_stats"
    )

    @property
    def total_points(self):
        return self.achievement_points + self.attendance_points


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    tg_id = Column(Integer, unique=True, nullable=False, index=True)

    username = Column(String(64), nullable=True)
    full_name = Column(String(255), nullable=True)

    is_admin = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    achievements = relationship(
        "UserAchievement",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserAchievement.user_id"
    )

    awarded = relationship(
        "UserAchievement",
        back_populates="awarded_by",
        foreign_keys="UserAchievement.awarded_by_user_id"
    )

    attendance_logs = relationship(
        "AttendanceLog",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    
    season_stats = relationship(
        "SeasonUserStat",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )


class Achievement(Base):
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True)

    title = Column(String(255), nullable=False)
    description = Column(String(1024), nullable=True)

    points = Column(Integer, default=0, nullable=False)

    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    is_open = Column(Boolean, default=True, nullable=False)

    season_id = Column(
        Integer,
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False
    )

    awards = relationship(
        "UserAchievement",
        back_populates="achievement",
        cascade="all, delete-orphan",
        foreign_keys="UserAchievement.achievement_id"
    )


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True)

    season_id = Column(
        Integer,
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    achievement_id = Column(
        Integer,
        ForeignKey("achievements.id", ondelete="CASCADE"),
        nullable=False
    )

    awarded_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    awarded_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "achievement_id",
            "season_id",
            name="uq_user_achievement_once_per_season"
        ),
    )

    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="achievements"
    )

    achievement = relationship(
        "Achievement",
        foreign_keys=[achievement_id],
        back_populates="awards"
    )

    awarded_by = relationship(
        "User",
        foreign_keys=[awarded_by_user_id],
        back_populates="awarded"
    )


class AttendanceLog(Base):
    __tablename__ = "attendance_log"

    id = Column(Integer, primary_key=True)

    season_id = Column(
        Integer,
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False
    )

    date = Column(Date, nullable=False, default=date.today)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    user = relationship(
        "User",
        back_populates="attendance_logs",
        passive_deletes=True
    )

    season = relationship("Season")
