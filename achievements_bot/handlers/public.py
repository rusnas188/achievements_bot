from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from functools import wraps

from achievements_bot.db.base import SessionLocal
from achievements_bot.services.achievement_service import AchievementService, NotFoundError
from achievements_bot.states.register_form import RegisterForm
from achievements_bot.utils import set_commands_for_user, pts_form
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from achievements_bot.db.models import User, SeasonUserStat, AttendanceLog, Season, Achievement, UserAchievement
from achievements_bot.config import get_settings
from sqlalchemy import desc


SETTINGS = get_settings()
router_public = Router()

# Словарь для хранения сообщений с клавиатурами
MESSAGES_WITH_KB: dict[int, dict[str, list[int]]] = {}

# Нужно удалять старые клавиатуры, чтобы кнопка "Назад" нормально работала
# Иначе пользователь будет тыкать разные клавиатуры, и кнопка будет работать криво
# Т.к. в состояния будут записываться состояние клавиатур и там будет мусор по сути
def with_kb_cleanup(menu_type: str):
    """
    menu_type: "user" или "admin"
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(event, *args, **kwargs):
            user_id = getattr(event.from_user, "id", None)
            if not user_id:
                return await func(event, *args, **kwargs)
            if user_id not in MESSAGES_WITH_KB:
                MESSAGES_WITH_KB[user_id] = {}
            if menu_type not in MESSAGES_WITH_KB[user_id]:
                MESSAGES_WITH_KB[user_id][menu_type] = []
            # Убираем старые клавиатуры только для нужного типа панели
            old_msg_ids = MESSAGES_WITH_KB[user_id][menu_type]
            for old_id in old_msg_ids:
                try:
                    await event.bot.edit_message_reply_markup(
                        chat_id=user_id,
                        message_id=old_id,
                        reply_markup=None
                    )
                except Exception:
                    pass
            MESSAGES_WITH_KB[user_id][menu_type] = []
            result = await func(event, *args, **kwargs)
            # Сохраняем id нового сообщения
            if isinstance(result, types.Message):
                MESSAGES_WITH_KB[user_id][menu_type].append(result.message_id)
            elif hasattr(event, "message") and event.message:
                MESSAGES_WITH_KB[user_id][menu_type].append(event.message.message_id)
            return result
        return wrapper
    return decorator

def add_close_button(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Добавляет кнопку 'Закрыть' в клавиатуру"""
    kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")])
    return kb

@router_public.callback_query(F.data == "close_menu")
async def close_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.delete()
    MESSAGES_WITH_KB.pop(user_id, None)


# =========================
# Keyboards
# =========================
def build_user_menu():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Мои ачивки", callback_data="my")],
        [InlineKeyboardButton(text="🏆 Таблица лидеров", callback_data="leaderboard")],
        [InlineKeyboardButton(text="🎖 Все ачивки", callback_data="achievements")],
        [InlineKeyboardButton(text="📂 Прошлые сезоны", callback_data="seasons")]
    ])

def back_to_previous_kb():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад", callback_data="back")]]
    )


@router_public.callback_query(F.data == "seasons")
async def show_seasons(callback: types.CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(
            tg_id=callback.from_user.id
        ).first()

        if not user:
            return await callback.message.edit_text(
                "Ты ещё не зарегистрирован. Напиши /start",
                reply_markup=None
            )
    
        seasons = (
            db.query(Season)
            .filter(Season.is_active == False)
            .order_by(Season.ended_at.desc())
            .all()
        )

        if not seasons:
            return await callback.message.edit_text(
                "📭 Прошлых сезонов пока нет.",
                reply_markup=back_to_previous_kb()
            )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=s.name,
                        callback_data=f"season_{s.id}"
                    )
                ]
                for s in seasons
            ] + [[InlineKeyboardButton(text="⬅ Назад", callback_data="back")]]
        )

        await callback.message.edit_text("📂 Выберите сезон:", reply_markup=kb)
    finally:
        db.close()


@router_public.callback_query(F.data.startswith("season_"))
async def show_season_details(callback: types.CallbackQuery, state: FSMContext):
    season_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    db = SessionLocal()
    try:
        # ===== FSM history =====
        data = await state.get_data()
        prev = data.get("prev", [])

        # Запоминаем, что пришли из списка сезонов
        if not prev or prev[-1] != "seasons":
            prev.append("seasons")

        await state.update_data(prev=prev)

        # ===== User =====
        user = db.query(User).filter_by(tg_id=user_id).first()
        if not user:
            return await callback.message.edit_text(
                "❌ Пользователь не найден.",
                reply_markup=back_to_previous_kb()
            )

        season = db.query(Season).get(season_id)
        if not season:
            return await callback.message.edit_text(
                "❌ Сезон не найден.",
                reply_markup=back_to_previous_kb()
            )

        stat = db.query(SeasonUserStat).filter_by(
            season_id=season_id,
            user_id=user.id
        ).first()

        if not stat:
            return await callback.message.edit_text(
                "❌ Нет данных за этот сезон.",
                reply_markup=back_to_previous_kb()
            )

        # ===== Attendance =====
        attendance_logs = db.query(AttendanceLog).filter_by(
            season_id=season_id,
            user_id=user.id
        ).count()

        attendance_points = stat.attendance_points
        achievement_points = stat.achievement_points

        # ===== Achievements =====
        user_achs = db.query(UserAchievement).filter_by(
            season_id=season_id,
            user_id=user.id
        ).all()

        # ===== Leaderboard place =====
        leaderboard = db.query(SeasonUserStat).filter_by(
            season_id=season_id
        ).order_by(
            desc(
                SeasonUserStat.attendance_points +
                SeasonUserStat.achievement_points
            )
        ).all()

        place = next(
            (i + 1 for i, s in enumerate(leaderboard) if s.user_id == user.id),
            "—"
        )

        # ===== Text build =====
        lines = []

        lines.append(f"📊 *Сезон:* {season.name}\n")
        lines.append(f"💰 Общие очки: {stat.total_points}")
        lines.append(f"📅 Посещения: {attendance_logs} \\[+{pts_form(attendance_points)}]")
        lines.append(f"🏆 Очки за ачивки: {achievement_points}")
        lines.append(f"🥇 Место: {place}\n")

        if user_achs:
            lines.append("🎖 Твои ачивки:\n")

            for ua in user_achs:
                ach = ua.achievement

                description = ""

                if ach.description:
                    if ach.is_open or user.is_admin:
                        description = ach.description
                    else:
                        description = ach.description[1:-1]

                lines.append(
                    f"🏅 {ach.title} [{'+' if ach.points > 0 else ''}{pts_form(ach.points)}]"
                )

                if description:
                    lines.append(description + "\n")
                
        else:
            lines.append("😢 Ачивок в этом сезоне нет.")

        text = "\n".join(lines)

        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=back_to_previous_kb()
        )

    finally:
        db.close()

# =========================
# /start и /rename
# =========================
@router_public.message(Command("start"))
@router_public.message(Command("rename"))
async def cmd_start(message: types.Message, state: FSMContext):
    await set_commands_for_user(message.bot, message.from_user.id)
    await state.set_state(RegisterForm.waiting_for_full_name)
    await message.answer("Привет! Напиши свои ФИ (например: Иванов Иван).")

@router_public.message(RegisterForm.waiting_for_full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    full_name = message.text.strip()
    db = SessionLocal()
    try:
        svc = AchievementService(db)
        svc.register_user(
            tg_id=message.from_user.id,
            full_name=full_name,
            username=message.from_user.username  # <-- передаём username
        )
    finally:
        db.close()
    await state.clear()
    await set_commands_for_user(message.bot, message.from_user.id)
    await message.answer("Регистрация завершена! Напиши /menu для открытия панели.")


# =========================
# /menu - панель пользователя
# =========================
@router_public.message(Command("menu"))
@with_kb_cleanup("user")
async def cmd_menu(message: types.Message):
    kb = build_user_menu()
    kb = add_close_button(kb)
    return await message.answer("Панель пользователя:", reply_markup=kb)

# =========================
# Обработка кнопок
# =========================
@router_public.callback_query(F.data.in_({"achievements", "leaderboard", "my"})) # "others", "my"
async def handle_user_panel(callback: types.CallbackQuery, state:FSMContext):
    db = SessionLocal()
    try:
        svc = AchievementService(db)
        user_id = callback.from_user.id
        
        try:
            season = svc.get_active_season()
            user, user_achievements = svc.list_user_achievements(user_id)
            user_achievements = svc.list_user_achievements_for_season(
                user.id,
                season.id
            )
        except NotFoundError:
            return await callback.message.edit_text("Ты ещё не зарегистрирован. Напиши /start", reply_markup=None)
        
        if callback.data == "achievements":

            achs = (
                db.query(Achievement)
                .filter(Achievement.season_id == season.id)
                .all()
            )
            if not achs:
                return await callback.message.edit_text("Пока нет доступных ачивок.", reply_markup=back_to_previous_kb())

            total_users = db.query(User).filter(User.is_admin == False).count()

            text = ""

            open_achs = [a for a in achs if a.is_open]
            if open_achs:
                text += "\n📜 Открытые ачивки:\n\n"
                for a in open_achs:
                    text += f"🏅 {a.title} [{'+' if a.points > 0 else ''}{pts_form(a.points)}]\n"
                    if a.description:
                        text += f"{a.description}\n"
                    awarded_count = sum(1 for award in a.awards if not award.user.is_admin) if hasattr(a, "awards") else 0
                    if awarded_count:
                        text += f"Есть у {awarded_count} из {total_users} пользовател{'я' if (total_users % 10 == 1 and total_users % 100 != 11) else 'ей'}\n\n"
                    else:
                        text += "Эту ачивку еще никто не получил\n\n"

            # Все скрытые ачивки
            hidden_achs = [a for a in achs if not a.is_open]
            if hidden_achs:
                text += "\n🔒 Секретные ачивки:\n\n"
                for a in hidden_achs:
                    text += f"🏅 {a.title} [{'+' if a.points > 0 else ''}{pts_form(a.points)}]\n"
                    awarded_count = sum(1 for award in a.awards if not award.user.is_admin) if hasattr(a, "awards") else 0
                    if a.description:
                        if user.is_admin:
                            text += f"{a.description[1:-1]}\n"

                    if awarded_count:
                        text += f"Есть у {awarded_count} из {total_users} пользовател{'я' if (total_users % 10 == 1 and total_users % 100 != 11) else 'ей'}\n\n"
                    else:
                        text += "Эту ачивку еще никто не получил\n\n"

            await callback.message.edit_text(text, reply_markup=back_to_previous_kb())



        elif callback.data == "my":

            # --- статистика сезона ---
            stat = db.query(SeasonUserStat).filter_by(
                season_id=season.id,
                user_id=user.id
            ).first()

            total_points = stat.total_points if stat else 0
            attendance_points = stat.attendance_points if stat else 0

            attendance_count = db.query(AttendanceLog).filter_by(
                season_id=season.id,
                user_id=user.id
            ).count()


            # --- если нет ачивок ---
            if not user_achievements:
                text = ""
                if attendance_count > 0:
                    text += "😢 У тебя пока нет ачивок\n\n"
                    text += "📅 Посещения: "
                    text += f"{attendance_count} мероприят"
                    text += f"{('ий' if (attendance_count % 10 in (5, 6, 7, 8, 9, 0) or attendance_count == 11) else ('ие' if attendance_count % 10 == 1 else 'ия'))} [+{pts_form(attendance_points)}]\n"
                    text += f"\n💰 Всего очков: {total_points}\n"
                else:
                    text += "😢 У тебя пока нет ачивок и посещений.\n"
                    text += "Приходи на мероприятия, чтобы получать очки и открывать ачивки!\n"
                await callback.message.edit_text(text, reply_markup=back_to_previous_kb())
                return

            # --- если есть ачивки ---
            text = "🏅 Твои ачивки:\n\n"
            for ua in user_achievements:
                a = ua.achievement
                text += f"🏆 {a.title} [{'+' if a.points > 0 else ''}{pts_form(a.points)}]\n"
                if a.description:
                    if a.is_open:
                        text += f"{a.description}\n\n"
                    else:
                        text += f"{a.description[1:-1]}\n\n"

            # Добавляем блок про посещения
            if attendance_count > 0:
                text += "📅 Посещения: "
                text += f"{attendance_count} мероприят"
                text += f"{('ий' if (attendance_count % 10 in (5, 6, 7, 8, 9, 0) or attendance_count == 11) else ('ие' if attendance_count % 10 == 1 else 'ия'))} [+{pts_form(attendance_points)}]\n\n"
            else:
                text += "😢 У тебя пока нет посещений.\n\n"

            text += f"💰 Всего очков: {total_points}\n"

            leaderboard = svc.get_leaderboard()
            place = "—"
            if stat:
                place = next(
                    (i + 1 for i, s in enumerate(leaderboard) if s.user_id == user.id),
                    "—"
                )
            text += f"🥇 Место в таблице: {place}\n"

            await callback.message.edit_text(text, reply_markup=back_to_previous_kb())


        elif callback.data == "leaderboard":
            top = svc.get_leaderboard()
            if not top:
                return await callback.message.edit_text("Таблица лидеров пуста.", reply_markup=back_to_previous_kb())
            text = "🏆 Топ игроков:\n\n"
            for i, stat in enumerate(top, start=1):
                user = stat.user
                name = user.full_name or f"@{user.username}" or str(user.tg_id)

                text += f"{i}. {name} — {pts_form(stat.total_points)}\n"
            await callback.message.edit_text(text, reply_markup=back_to_previous_kb())

                

    finally:
        db.close()


@router_public.callback_query(F.data == "back")
async def handle_back(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    prev = data.get("prev", [])

    # Первый уровень — просто главное меню
    if not prev:
        kb = build_user_menu()
        kb = add_close_button(kb)
        await callback.message.edit_text(
            "Панель пользователя:",
            reply_markup=kb
        )
        await state.clear()
        return

    last = prev.pop()
    await state.update_data(prev=prev)

    if last == "seasons":
        return await show_seasons(callback, state)
