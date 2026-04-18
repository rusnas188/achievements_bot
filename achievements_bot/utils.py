from aiogram import Bot, types
from achievements_bot.db.base import SessionLocal
from achievements_bot.db.models import User
from achievements_bot.commands import USER_COMMANDS, ADMIN_COMMANDS
from datetime import datetime, timedelta


async def set_commands_for_user(bot: Bot, tg_id: int):
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(tg_id=tg_id).first()
        commands = USER_COMMANDS.copy()

        if user and user.is_admin:
            commands.update(ADMIN_COMMANDS)

        tg_commands = [types.BotCommand(command=cmd, description=desc) 
                       for cmd, desc in commands.items()]

        await bot.set_my_commands(
            tg_commands,
            scope=types.BotCommandScopeChat(chat_id=tg_id)
        )
    finally:
        session.close()

def pts_form(num: int) -> str:
    abs_num = abs(num)
    if (abs_num % 100 > 19 or abs_num % 100 < 11) and (abs_num % 10 in (1, 2, 3, 4)):
        if abs_num % 10 == 1:
            return f"{num} очко"
        return f"{num} очка"
    return f"{num} очков" 

def current_date_gmt3():
    return (datetime.utcnow() + timedelta(hours=3)).date()