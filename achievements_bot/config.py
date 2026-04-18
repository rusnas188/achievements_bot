import os
from dataclasses import dataclass
from configparser import ConfigParser


@dataclass
class Settings:
    bot_token: str
    database_url: str
    admin_tg_id: int
    logs_path: str


def load_settings_from_file() -> Settings:
    """Загружает настройки из settings.conf в глобальный объект _settings."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "settings.conf")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"⚠️ Не найден файл настроек: {config_path}")

    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")
    return Settings(
        bot_token=parser.get("bot", "bot_token"),
        database_url=parser.get("database", "database_url"),
        admin_tg_id=parser.getint("bot", "admin_tg_id"),
        logs_path=parser.get("logs", "logs_path"),
    )

_settings = load_settings_from_file()

def get_settings() -> Settings:
    """Возвращает загруженные настройки."""
    if _settings is None:
        raise RuntimeError("⚠️ Настройки не загружены.")
    return _settings
