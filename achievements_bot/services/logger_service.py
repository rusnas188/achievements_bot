import logging
import os
from datetime import datetime, timedelta
from achievements_bot.config import get_settings

from achievements_bot.utils import pts_form

SETTINGS = get_settings()

# Настраиваем логгер
logger = logging.getLogger("achievements_logger")
logger.setLevel(logging.INFO)

class FixedOffsetFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        # добавляем 3 часа
        dt = datetime.fromtimestamp(record.created) + timedelta(hours=3)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.isoformat()

file_handler = logging.FileHandler(SETTINGS.logs_path, encoding="utf-8")
formatter = FixedOffsetFormatter("%(asctime)s — %(message)s", datefmt="%d-%m-%Y %H:%M:%S")
file_handler.setFormatter(formatter)


if not logger.hasHandlers():
    logger.addHandler(file_handler)


def log_create_achievement(admin_name: str, ach_title: str, ach_points: str, ach_description: str):
    if not ach_description: ach_description = "-"
    logger.info(f"{admin_name} создал ачивку '{ach_title}' [{'+' if ach_points > 0 else ''}{pts_form(ach_points)}] '{ach_description}'")

def log_edit_achievement(admin_name: str, ach_title: str, ach_points: str, ach_description: str, field: str, old_value: str):
    field = ("описание" if field == "edit_desc" else ("очки" if field == "edit_points" else "название"))
    if field == "описание" and not old_value: old_value = "-"
    if not ach_description: ach_description = "-"
    logger.info(f"{admin_name} отредактировал ачивку '{ach_title}' [{'+' if ach_points > 0 else ''}{pts_form(ach_points)}] '{ach_description}'; старое значение '{old_value}' в поле '{field}'")

def log_delete_achievement(admin_name: str, ach_title: str, ach_points: str, ach_description: str):
    if not ach_description: ach_description = "-"
    logger.info(f"{admin_name} удалил ачивку '{ach_title}' [{'+' if ach_points > 0 else ''}{pts_form(ach_points)}] '{ach_description}'")

def log_grant_achievement(admin_name: str, ach_title: str, ach_points: str, ach_description: str, user_name: str):
    if not ach_description: ach_description = "-"
    logger.info(f"{admin_name} выдал ачивку '{ach_title}' [{'+' if ach_points > 0 else ''}{pts_form(ach_points)}] '{ach_description} пользователю {user_name}")

def log_revoke_achievement(admin_name: str, ach_title: str, ach_points: str, ach_description: str, user_name: str):
    if not ach_description: ach_description = "-"
    logger.info(f"{admin_name} отобрал ачивку '{ach_title}' [{'+' if ach_points > 0 else ''}{pts_form(ach_points)}] '{ach_description} у пользователя {user_name}")

def log_attendance(admin_name: str, user_names: list[str], points: int = 5):
    if not user_names:
        return

    users_str = ", ".join(user_names)
    logger.info(f"{admin_name} начислил +{points} баллов за посещение пользователям: {users_str}")
