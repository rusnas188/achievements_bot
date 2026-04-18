import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from achievements_bot.config import get_settings
from achievements_bot.handlers.public import router_public
from achievements_bot.handlers.admin import router_admin
from achievements_bot.db.init_db import init_db


async def on_startup(bot: Bot):
    pass

async def main() -> None:
    init_db()
    bot = Bot(token=get_settings().bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router_public)
    dp.include_router(router_admin)
    await on_startup(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())