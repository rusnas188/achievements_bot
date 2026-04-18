from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import CallbackData
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import desc

from achievements_bot.services import logger_service as log

from achievements_bot.db.base import SessionLocal
from achievements_bot.db.models import User, Achievement, SeasonUserStat, Season
from achievements_bot.services.achievement_service import (
    AchievementService,
    AuthError,
    NotFoundError,
    AlreadyHasAchievement,
)
from achievements_bot.utils import set_commands_for_user, pts_form, current_date_gmt3
from achievements_bot.db.models import AttendanceLog

from achievements_bot.handlers.public import add_close_button, with_kb_cleanup, MESSAGES_WITH_KB, SETTINGS
from aiogram import types
from functools import wraps

from aiogram.types import InputMediaDocument, FSInputFile
import os


class SelectUsersFSM(StatesGroup):
    choosing = State()
    waiting_for_points = State()  # 👈 новое состояние

class MultiUserSelectCb(CallbackData, prefix="mus"):
    action: str  # например, "toggle" или "confirm"
    tg_id: int | None = None  # tg_id пользователя, optional для confirm

class UserSelectCb(CallbackData, prefix="user"):
    action: str
    tg_id: int

class FinishSeasonForm(StatesGroup):
    waiting_for_name = State()


class AdminActionCb(CallbackData, prefix="admin"):
    action: str

class AchievementSelectCb(CallbackData, prefix="achv"):
    action: str
    tg_id: int
    ach_id: int

class AchEditMenuCb(CallbackData, prefix="achedit"):
    action: str
    ach_id: int

class AddAchievementFSM(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_points = State()


class EditAchievementFSM(StatesGroup):
    waiting_for_value = State()


def admin_required(func):
    @wraps(func)
    async def wrapper(event: types.Message | types.CallbackQuery, *args, **kwargs):
        db = SessionLocal()
        try:
            user_id = (
                event.from_user.id
                if hasattr(event, "from_user")
                else event.message.from_user.id
            )
            me = db.query(User).filter_by(tg_id=user_id).first()
            if not me or not me.is_admin:
                # Для Message и CallbackQuery по-разному отвечаем
                if isinstance(event, types.Message):
                    await event.answer("❌ У вас нет прав администратора.")
                else:
                    await event.message.answer("❌ У вас нет прав администратора.")
                return
        finally:
            db.close()
        return await func(event, *args, **kwargs)
    return wrapper



router_admin = Router()


def build_users_keyboard(db, action: str, back_action:str, exclude_tg_id: int | None = None, users: list[User] | None = None) -> InlineKeyboardMarkup:
    if users is None:
        users = db.query(User).all()
    rows = []
    users = sorted(users, key=lambda u: (u.full_name or u.username or str(u.tg_id)).lower())
    for u in users:
        if exclude_tg_id and u.tg_id == exclude_tg_id:
            continue  # пропускаем себя

        role = "👑" if u.tg_id == SETTINGS.admin_tg_id else ("⭐" if u.is_admin else "👤")
        tg_display = f"@{u.username}" if u.username else str(u.tg_id)
        rows.append([
            InlineKeyboardButton(
                text=f"{role} {u.full_name or 'Без имени'} ({tg_display})",
                callback_data=UserSelectCb(action=action, tg_id=u.tg_id).pack(),
            )
        ])
    return InlineKeyboardMarkup(
        inline_keyboard=rows + [[InlineKeyboardButton(text="⬅ Назад", callback_data=back_action)]]
    )


def build_users_keyboard_multi(
    users: list[User],
    selected: set[int],
    back_action:str,
    points: int,
) -> InlineKeyboardMarkup:
    """
    Клавиатура с чекбоксами для мультивыбора пользователей
    """
    rows = []
    users = sorted(users, key=lambda u: (u.full_name or u.username or str(u.tg_id)).lower())
    for u in users:
        role = "👑" if u.tg_id == SETTINGS.admin_tg_id else ("⭐" if u.is_admin else "👤")
        tg_display = f"@{u.username}" if u.username else str(u.tg_id)
        mark = "✅" if u.tg_id in selected else "⬜"

        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {role} {u.full_name or 'Без имени'} ({tg_display})",
                callback_data=MultiUserSelectCb(action="toggle", tg_id=u.tg_id).pack(),  # просто переключение чекбокса
            )
        ])

    # подтверждение отдельной кнопкой
    rows.append([
        InlineKeyboardButton(text="✔ Подтвердить", callback_data="confirm")
    ])
    rows.append([
    InlineKeyboardButton(
        text=f"💎 Баллы: {points}",
        callback_data="change_attendance_points"
    )
    ])
    rows.append([
        InlineKeyboardButton(text="⬅ Назад", callback_data=back_action)
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_default_attendance_points():
    today = current_date_gmt3()
    # weekday(): 0 = понедельник, 5 = суббота
    if today.weekday() == 5:
        return 10
    return 5


@router_admin.callback_query(AdminActionCb.filter(F.action == "finish_season"))
@admin_required
async def finish_season_start(callback: types.CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        season = db.query(Season).filter_by(is_active=True).first()

        if not season:
            await callback.message.answer("❌ Активный сезон не найден.")
            return

        # 🔥 сохраняем ID сезона в FSM
        await state.update_data(finishing_season_id=season.id)

        await state.set_state(FinishSeasonForm.waiting_for_name)

        await callback.message.edit_text(
            "🏁 Введите название завершённого сезона",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]
                ]
            )
        )
    finally:
        db.close()


@router_admin.message(FinishSeasonForm.waiting_for_name)
@admin_required
async def finish_season_process(message: types.Message, state: FSMContext):
    db = SessionLocal()
    try:
        season_name = message.text.strip()

        if not season_name:
            await message.answer("❌ Название не может быть пустым.")
            return

        # 🔹 Убираем старую клавиатуру (ВАЖНО)
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.message_id - 1,
                reply_markup=None
            )
        except Exception:
            pass

        # 🔥 получаем ID сезона из FSM
        data = await state.get_data()
        season_id = data.get("finishing_season_id")

        if not season_id:
            await message.answer("⚠️ Сезон не выбран или уже завершён.")
            await state.clear()
            return

        season = db.query(Season).filter_by(
            id=season_id,
            is_active=True
        ).first()

        # Если сезон уже закрыт (например, повторное сообщение)
        if not season:
            await message.answer("⚠️ Этот сезон уже завершён.")
            await state.clear()
            return

        # 2️⃣ Закрываем его
        season.name = season_name
        season.is_active = False
        season.ended_at = current_date_gmt3()

        # 3️⃣ Формируем лидерборд
        stats = (
            db.query(SeasonUserStat)
            .filter_by(season_id=season.id)
            .order_by(
                desc(
                    SeasonUserStat.attendance_points +
                    SeasonUserStat.achievement_points
                )
            )
            .all()
        )

        leaderboard_text = f"🏆 Итоги сезона: {season_name}\n\n"

        for i, stat in enumerate(stats, start=1):
            leaderboard_text += (
                f"{i}. {stat.user.full_name or stat.user.username} "
                f"— {stat.total_points} очков\n"
            )


        # 5️⃣ Создаём новый сезон
        new_season = Season(
            name="Новый сезон",
            started_at=current_date_gmt3(),
            is_active=True
        )
        db.add(new_season)

        db.commit()

        # 6️⃣ Рассылка лидерборда
        users = db.query(User).all()

        for user in users:
            try:
                await message.bot.send_message(
                    chat_id=user.tg_id,
                    text=leaderboard_text
                )
            except Exception:
                pass
        
        # 7️⃣ Отправляем logs.txt админу
        logs_path = SETTINGS.logs_path

        if os.path.exists(logs_path) and os.path.getsize(logs_path) > 0:
            try:
                log_file = FSInputFile(logs_path)
                await message.bot.send_document(
                    chat_id=message.chat.id,
                    document=log_file,
                    caption="📄 Логи завершённого сезона"
                )

                # 🧹 Очищаем файл после отправки
                with open(logs_path, "w", encoding="utf-8") as f:
                    f.write("")

            except Exception as e:
                await message.answer(f"⚠️ Не удалось отправить logs.txt\n{e}")
        else:
            await message.answer("ℹ️ Файл logs.txt пуст или не найден.")

    finally:
        db.close()
        await state.clear()


@router_admin.callback_query(F.data == "change_attendance_points")
@admin_required
async def change_attendance_points(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()

    # Переходим в состояние ввода баллов
    await state.set_state(SelectUsersFSM.waiting_for_points)

    msg = await callback.message.edit_text(
        "💎 Введите количество баллов за посещение:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]
            ]
        )
    )

    await state.update_data(points_message_id=msg.message_id)

    await callback.answer()

@router_admin.message(SelectUsersFSM.waiting_for_points)
@with_kb_cleanup("admin")
@admin_required
async def set_attendance_points(message: types.Message, state: FSMContext):
    # ===== 1. Валидация ввода =====
    try:
        points = int((message.text or "").strip())
        if points < 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            "⚠ Введите положительное целое число:",
            reply_markup=back_to_previous_kb()
        )

    data = await state.get_data()

    # ===== 2. Убираем клавиатуру у окна ввода =====
    points_message_id = data.get("points_message_id")

    if points_message_id:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=points_message_id,
                reply_markup=None
            )
        except Exception:
            pass  # если вдруг уже удалено — не критично

    # ===== 3. Сохраняем новые баллы =====
    await state.update_data(
        attendance_points=points,
        points_message_id=None  # больше не нужно
    )

    await state.set_state(SelectUsersFSM.choosing)

    # ===== 4. Восстанавливаем окно выбора пользователей =====
    selected = set(data.get("selected", []))
    users = data.get("users", [])
    header_text = data.get("header_text", "")

    full_text = f"{header_text}👥 Выберите пользователей, которые сегодня пришли:"

    await message.answer(
        full_text,
        reply_markup=build_users_keyboard_multi(
            users,
            selected,
            back_action="admin_back",
            points=points
        )
    )

@router_admin.callback_query(AdminActionCb.filter(F.action == "attendance"))
@admin_required
async def handle_attendance(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    db = SessionLocal()
    try:
        today = current_date_gmt3()

        all_users = db.query(User).filter(User.is_admin == False).all()
        season = db.query(Season).filter_by(is_active=True).first()

        attended_user_ids = {
            log.user_id
            for log in db.query(AttendanceLog).filter(
                AttendanceLog.date == today,
                AttendanceLog.season_id == season.id
            ).all()
        }

        attended_users = [u for u in all_users if u.id in attended_user_ids]
        available_users = [u for u in all_users if u.id not in attended_user_ids]

        # Формируем текст
        base_text = "👥 Выберите пользователей, которые сегодня пришли:"
        header_text = ""
        if attended_users:
            already = ", ".join(u.full_name or f"@{u.username}" or str(u.tg_id) for u in attended_users)
            header_text = f"✅ Посещения уже отмечены у: {already}\n\n"

        full_text = header_text + base_text

        default_points = get_default_attendance_points()

        await state.set_state(SelectUsersFSM.choosing)
        await state.update_data(
            selected=set(),
            action="attendance",
            users=available_users,
            header_text=header_text,
            attendance_points=default_points  # 👈 сохраняем баллы
        )
        await callback.message.edit_text(
            full_text,
            reply_markup=build_users_keyboard_multi(
                available_users,
                set(),
                back_action="admin_back",
                points=default_points
            )
        )
    finally:
        db.close()



@with_kb_cleanup("admin")
@admin_required
@router_admin.callback_query(MultiUserSelectCb.filter(F.action == "toggle"))
async def toggle_user(callback: types.CallbackQuery, callback_data: MultiUserSelectCb, state: FSMContext):

    data = await state.get_data()

    points = data.get("attendance_points", get_default_attendance_points())
    tg_id = callback_data.tg_id

    selected: set[int] = set(data.get("selected", []))
    header_text: str = data.get("header_text", "")
    users = data.get("users", [])

    if tg_id in selected:
        selected.remove(tg_id)
    else:
        selected.add(tg_id)

    await state.update_data(selected=selected)

    full_text = f"{header_text}👥 Выберите пользователей, которые сегодня пришли:"

    await callback.message.edit_text(
        full_text,
        reply_markup=build_users_keyboard_multi(
            users,
            selected,
            back_action="admin_back",
            points=points
        )
    )
    await callback.answer()


@router_admin.callback_query(F.data == "confirm")
@with_kb_cleanup("admin")
@admin_required
async def confirm_multi(callback: types.CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        svc = AchievementService(db)

        data = await state.get_data()
        points = data.get("attendance_points", get_default_attendance_points())
        selected: set[int] = set(data.get("selected", []))

        if not selected:
            await callback.message.edit_text("❌ Никого не выбрано.")
            return

        today = current_date_gmt3()

        # 🔥 Активный сезон
        season = svc.get_active_season()
        if not season or not season.is_active:
            await callback.message.edit_text("❌ Нет активного сезона.")
            return

        affected = []
        already_marked = []
        failed = []

        users = db.query(User).filter(User.tg_id.in_(selected)).all()
        for user in users:
            # ✅ Проверка: уже отмечен сегодня в ЭТОМ сезоне
            already = db.query(AttendanceLog).filter_by(
                user_id=user.id,
                date=today,
                season_id=season.id
            ).first()

            if already:
                already_marked.append(user)
                continue

            # ✅ Лог посещения
            db.add(
                AttendanceLog(
                    user_id=user.id,
                    date=today,
                    season_id=season.id
                )
            )
            # 🔥 ГАРАНТИРУЕМ наличие статистики
            stat = svc.ensure_season_stat_exists(
                season_id=season.id,
                user_id=user.id
            )

            # ✅ Начисляем очки
            stat.attendance_points += points

            affected.append(user)

        # 🔥 Один коммит на всё
        db.commit()

        # === ЛОГИ ===
        if affected:
            admin_name = (
                f"@{callback.from_user.username}"
                if callback.from_user.username
                else f"id={callback.from_user.id}"
            )
            affected_user_names = [
                f"@{u.username}" if u.username else str(u.tg_id)
                for u in affected
            ]
            log.log_attendance(admin_name, affected_user_names, points=points)

        # === Уведомления пользователям ===
        for u in affected:
            try:
                stat = db.query(SeasonUserStat).filter_by(
                    season_id=season.id,
                    user_id=u.id
                ).first()

                total_points = stat.total_points if stat else 0

                msg = (
                    f"📅 Спасибо за участие!\n"
                    f"Тебе начислено +{pts_form(points)} за сегодняшнее посещение.\n"
                    f"💎 Всего очков в сезоне: {total_points}"
                )

                await callback.bot.send_message(chat_id=u.tg_id, text=msg)

            except Exception:
                failed.append(u)

        # === Ответ админу ===
        text_parts = []

        if affected:
            affected_names = ", ".join(
                u.full_name or f"@{u.username}" or str(u.tg_id)
                for u in affected
            )
            text_parts.append(
                f"✅ Проставлено посещение и начислено по {points} баллов: {affected_names}"
            )

        if already_marked:
            already_names = ", ".join(
                u.full_name or f"@{u.username}" or str(u.tg_id)
                for u in already_marked
            )
            text_parts.append(
                f"ℹ Уже отмечены сегодня: {already_names}"
            )

        if failed:
            failed_names = ", ".join(
                u.full_name or f"@{u.username}" or str(u.tg_id)
                for u in failed
            )
            text_parts.append(
                f"⚠️ Не удалось отправить уведомление пользователям: {failed_names}"
            )

        if not text_parts:
            text_parts.append("ℹ Никого не выбрано или все уже отмечены.")

        await callback.message.edit_text("\n\n".join(text_parts))

    finally:
        db.close()
        await state.clear()
        return await cmd_admin_panel(callback)





# =========================
# Keyboards
# =========================
def build_admin_menu(user_id: int) -> InlineKeyboardMarkup:
    if user_id == SETTINGS.admin_tg_id:
        keyboard = [
            [InlineKeyboardButton(text="➕ Создать ачивку", callback_data=AdminActionCb(action="add_achievement").pack()),
            InlineKeyboardButton(text="✏️ Редактировать ачивку", callback_data=AdminActionCb(action="edit_achievement").pack()),
            InlineKeyboardButton(text="🗑 Удалить ачивку", callback_data=AdminActionCb(action="delete_achievement").pack())],
            [InlineKeyboardButton(text="🎖 Выдать ачивку", callback_data=AdminActionCb(action="grant").pack()), 
            InlineKeyboardButton(text="🧹 Отозвать ачивку", callback_data=AdminActionCb(action="revoke").pack())],
            [InlineKeyboardButton(text="⭐ Сделать админом", callback_data=AdminActionCb(action="make_admin").pack()),
             InlineKeyboardButton(text="🚫 Снять админку", callback_data=AdminActionCb(action="remove_admin").pack())],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data=AdminActionCb(action="users").pack()),
            InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=AdminActionCb(action="delete_user").pack())],
            [InlineKeyboardButton(text="✍️ Отметить посещение", callback_data=AdminActionCb(action="attendance").pack())],
            [InlineKeyboardButton(text="🏁 Завершить сезон", callback_data=AdminActionCb(action="finish_season").pack())],
            [InlineKeyboardButton(text="📑 Логи", callback_data=AdminActionCb(action="logs").pack())]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton(text="➕ Создать ачивку", callback_data=AdminActionCb(action="add_achievement").pack()),
            InlineKeyboardButton(text="✏️ Редактировать ачивку", callback_data=AdminActionCb(action="edit_achievement").pack()),
            InlineKeyboardButton(text="🗑 Удалить ачивку", callback_data=AdminActionCb(action="delete_achievement").pack())],
            [InlineKeyboardButton(text="🎖 Выдать ачивку", callback_data=AdminActionCb(action="grant").pack()), 
            InlineKeyboardButton(text="🧹 Отозвать ачивку", callback_data=AdminActionCb(action="revoke").pack())],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data=AdminActionCb(action="users").pack())],
            [InlineKeyboardButton(text="✍️ Отметить посещение", callback_data=AdminActionCb(action="attendance").pack())]
        ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_achievements_keyboard(db, action: str, tg_id: int = 0) -> InlineKeyboardMarkup:
    season = db.query(Season).filter_by(is_active=True).first()

    if not season:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Нет активного сезона", callback_data="admin_back")]]
        )

    achs = db.query(Achievement).filter_by(season_id=season.id).all()
    rows = []
    achs = sorted(achs, key=lambda a: a.title.lower())
    for a in achs:
        rows.append([
            InlineKeyboardButton(
                text=f"{a.title} [{'+' if a.points > 0 else ''}{a.points}]",
                callback_data=AchievementSelectCb(action=action, tg_id=tg_id, ach_id=a.id).pack(),
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows + [[InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]])


# =========================
# /admin entrypoint
# =========================
@router_admin.message(Command("admin"))
@with_kb_cleanup("admin")
@admin_required
async def cmd_admin_panel(event: types.Message | types.CallbackQuery, from_back=False):
    kb = build_admin_menu(event.from_user.id)
    kb = add_close_button(kb)

    if isinstance(event, types.CallbackQuery):
        if from_back:
            return await event.message.edit_text("⚙ Админ-панель:", reply_markup=kb)
        return await event.message.answer("⚙ Админ-панель:", reply_markup=kb)
    return await event.answer("⚙ Админ-панель:", reply_markup=kb)


def back_to_previous_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]
        ]
    )


# =========================
# Создание ачивки
# =========================
@router_admin.callback_query(AdminActionCb.filter(F.action == "add_achievement"))
@with_kb_cleanup("admin")
@admin_required
async def add_achievement_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddAchievementFSM.waiting_for_title)
    await state.update_data(prev=[])

    return await callback.message.edit_text(
        "➕ Создание ачивки — введите *название*:",
        parse_mode="Markdown",
        reply_markup=back_to_previous_kb()
    )

@router_admin.message(AddAchievementFSM.waiting_for_title)
@with_kb_cleanup("admin")
@admin_required
async def add_achievement_title(message: types.Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("⚠ Введите непустое название:")
        return
    data = await state.get_data()
    prev = data.get("prev", [])
    prev.append({"state": AddAchievementFSM.waiting_for_title, "text": "Введите название:"})
    await state.update_data(title=title, prev=prev)
    await state.set_state(AddAchievementFSM.waiting_for_description)
    return await message.answer("Введите *описание* ачивки (можно пропустить, отправив '-') (можно сделать описание скрытым, написав его в скобочках '()'):",
                         parse_mode="Markdown",
                         reply_markup=back_to_previous_kb())
    

@router_admin.message(AddAchievementFSM.waiting_for_description)
@with_kb_cleanup("admin")
@admin_required
async def add_achievement_description(message: types.Message, state: FSMContext):
    description = None if (message.text or "").strip() == "-" else (message.text or "").strip()
    data = await state.get_data()
    prev = data.get("prev", [])
    prev.append({"state": AddAchievementFSM.waiting_for_description, "text": "Введите описание:"})
    await state.update_data(description=description, prev=prev)
    await state.set_state(AddAchievementFSM.waiting_for_points)
    return await message.answer("Введите *очки* (целое число):",
                         parse_mode="Markdown",
                         reply_markup=back_to_previous_kb())
    

@router_admin.message(AddAchievementFSM.waiting_for_points)
@with_kb_cleanup("admin")
@admin_required
async def add_achievement_points(message: types.Message, state: FSMContext):
    try:
        points = int((message.text or "").strip())
    except ValueError:
        return await message.answer("⚠ Очки должны быть целым числом. Введите ещё раз:",
                             reply_markup=back_to_previous_kb())

    data = await state.get_data()
    db = SessionLocal()
    try:
        is_open = True
        if data["description"] and data["description"].startswith("(") and data["description"].endswith(")"):
            is_open = False
        season = db.query(Season).filter_by(is_active=True).first()

        if not season:
            await message.answer("❌ Нет активного сезона.")
            await state.clear()
            return

        ach = Achievement(
            title=data["title"],
            description=data.get("description"),
            points=points,
            is_open=is_open,
            season_id=season.id
        )
        db.add(ach)
        db.commit()
        admin_name = f"@{message.from_user.username}" if message.from_user.username else f"id={message.from_user.id}"
        log.log_create_achievement(admin_name, ach.title, ach.points, ach.description)
        await message.answer(f"✅ Ачивка *{ach.title}* [{'+' if ach.points > 0 else ''}{pts_form(ach.points)}] создана.")
    finally:
        db.close()
        await state.clear()
        return await cmd_admin_panel(message)

# =========================
# Редактирование ачивки
# =========================
@admin_required
async def show_edit_achievements_menu(callback: types.CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        season = db.query(Season).filter_by(is_active=True).first()

        if not season:
            await callback.message.edit_text("❌ Нет активного сезона.")
            await cmd_admin_panel(callback)
            await state.clear()
            return

        achs = db.query(Achievement).filter_by(season_id=season.id).all()
        if not achs:
            await callback.message.edit_text("❌ Ачивок нет.", reply_markup=None)

            await cmd_admin_panel(callback)
            await state.clear()

            return
        
        achs = sorted(achs, key=lambda a: a.title.lower())
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"{a.title} [{'+' if a.points > 0 else ''}{a.points}]",
                    callback_data=AchEditMenuCb(action="menu", ach_id=a.id).pack()
                )] for a in achs
            ] + [[InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]]
        )

        await callback.message.edit_text("✏️ Выберите ачивку для редактирования:", reply_markup=kb)
    finally:
        db.close()


@router_admin.callback_query(AchEditMenuCb.filter(F.action == "menu"))
@admin_required
async def edit_achievement_menu(callback: types.CallbackQuery, callback_data: AchEditMenuCb, state: FSMContext):
    ach_id = callback_data.ach_id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=AchEditMenuCb(action="edit_title", ach_id=ach_id).pack())],
            [InlineKeyboardButton(text="📝 Изменить описание", callback_data=AchEditMenuCb(action="edit_desc", ach_id=ach_id).pack())],
            [InlineKeyboardButton(text="🔢 Изменить очки", callback_data=AchEditMenuCb(action="edit_points", ach_id=ach_id).pack())],
            [InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]
        ]
    )
    # edit_achievement_menu
    data = await state.get_data()
    prev = data.get("prev", [])
    prev.append({"state": "achievements_list"})  # или любое уникальное значение для выбора ачивки
    await state.update_data(edit_ach_id=ach_id, prev=prev)
    await callback.message.edit_text(f"Выберите, что изменить у ачивки:", reply_markup=kb)
    
    

@router_admin.callback_query(AchEditMenuCb.filter(F.action.in_({"edit_title", "edit_desc", "edit_points"})))
@with_kb_cleanup("admin")
@admin_required
async def edit_achievement_ask_value(callback: types.CallbackQuery, callback_data: AchEditMenuCb, state: FSMContext):
    ach_id = callback_data.ach_id
    action = callback_data.action
    await state.update_data(edit_field=action)

    prompt = {
        "edit_title": "Введите новое *название*:",
        "edit_desc": "Введите новое *описание* (или - чтобы очистить) (чтобы сделать описание скрытым, напишите его в скобочках '()'):",
        "edit_points": "Введите новое количество *очков* (целое число):",
    }[action]

    data = await state.get_data()
    prev = data.get("prev", [])
    prev.append({"state": "edit_menu"})
    await state.update_data(prev=prev)
    await state.set_state(EditAchievementFSM.waiting_for_value)
    return await callback.message.edit_text(
        prompt,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]]
        )
    )
    

@router_admin.message(EditAchievementFSM.waiting_for_value)
@with_kb_cleanup("admin")
@admin_required
async def edit_achievement_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ach_id = data.get("edit_ach_id")
    field = data.get("edit_field")
    value = (message.text or "").strip()
    db = SessionLocal()
    ach = None
    try:
        ach = db.query(Achievement).get(ach_id)
    except:
        pass
    if not ach:
        await message.answer("❌ Ачивка не найдена.")
        await state.clear()
        return

    delta = 0
    if field == "edit_title":
        if not value:
            await message.answer("⚠ Название не может быть пустым.")
            return
        old_value = ach.title
        ach.title = value
    elif field == "edit_desc":
        old_value = ach.description
        ach.description = None if value == "-" else value
        if value and value.startswith("(") and value.endswith(")"):
            ach.is_open = False
    elif field == "edit_points":
        try:
            new_points = int(value)
            delta = new_points - ach.points
            old_value = str(ach.points)
            ach.points = new_points
        except ValueError:
            return await message.answer("⚠ Очки должны быть целым числом. Введите ещё раз:",
                            reply_markup=back_to_previous_kb())
                
    try:
        db.commit()

        if field == "edit_points" and delta != 0:
            for ua in ach.awards:
                stat = db.query(SeasonUserStat).filter_by(
                    season_id=ua.season_id,
                    user_id=ua.user_id
                ).first()

                if stat:
                    stat.achievement_points += delta
            db.commit()

        admin_name = f"@{message.from_user.username}" if message.from_user.username else f"id={message.from_user.id}"
        log.log_edit_achievement(admin_name, ach.title, ach.points, ach.description, field, old_value)

        await message.answer("✅ Изменения сохранены.", reply_markup=None)
    finally:
        db.close()
        await state.clear()
        return await cmd_admin_panel(message)

# =========================
# Обработка кнопки "Назад"
# =========================
@router_admin.callback_query(F.data == "admin_back")
@admin_required
async def handle_back(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    # Если мы вводим баллы — вернуть к выбору пользователей
    if current_state == SelectUsersFSM.waiting_for_points.state:
        data = await state.get_data()

        await state.set_state(SelectUsersFSM.choosing)

        selected = set(data.get("selected", []))
        users = data.get("users", [])
        header_text = data.get("header_text", "")
        points = data.get("attendance_points", get_default_attendance_points())

        full_text = f"{header_text}👥 Выберите пользователей, которые сегодня пришли:"

        await callback.message.edit_text(
            full_text,
            reply_markup=build_users_keyboard_multi(
                users,
                selected,
                back_action="admin_back",
                points=points
            )
        )
        return

    data = await state.get_data()
    prev = data.get("prev", [])

    if not prev:
        await cmd_admin_panel(callback, from_back=True)
        await state.clear()
        return

    last = prev.pop()
    await state.update_data(prev=prev)

    # Обновляем состояние
    if last.get("state") == AddAchievementFSM.waiting_for_title:
        await state.set_state(AddAchievementFSM.waiting_for_title)
        await callback.message.edit_text("➕ Создание ачивки — введите *название*:", reply_markup=back_to_previous_kb())
    elif last.get("state") == AddAchievementFSM.waiting_for_description:
        await state.set_state(AddAchievementFSM.waiting_for_description)
        await callback.message.edit_text("Введите *описание* ачивки (можно пропустить, отправив '-') (можно сделать описание скрытым, написав его в скобочках '()'):", reply_markup=back_to_previous_kb())
    elif last.get("state") == AddAchievementFSM.waiting_for_points:
        await state.set_state(AddAchievementFSM.waiting_for_points)
        await callback.message.edit_text("Введите *очки*:", reply_markup=back_to_previous_kb())
    elif last.get("state") == "attendance_select":
        # Возврат к выбору пользователей при изменении баллов
        await state.set_state(SelectUsersFSM.choosing)

        selected = set(data.get("selected", []))
        users = data.get("users", [])
        header_text = data.get("header_text", "")
        points = data.get("attendance_points", get_default_attendance_points())

        full_text = f"{header_text}👥 Выберите пользователей, которые сегодня пришли:"

        await callback.message.edit_text(
            full_text,
            reply_markup=build_users_keyboard_multi(
                users,
                selected,
                back_action="admin_back",
                points=points
            )
        )
    elif last.get("state") == "edit_menu":
        ach_id = data.get("edit_ach_id")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Изменить название", callback_data=AchEditMenuCb(action="edit_title", ach_id=ach_id).pack())],
                [InlineKeyboardButton(text="📝 Изменить описание", callback_data=AchEditMenuCb(action="edit_desc", ach_id=ach_id).pack())],
                [InlineKeyboardButton(text="🔢 Изменить очки", callback_data=AchEditMenuCb(action="edit_points", ach_id=ach_id).pack())],
                [InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]
            ]
        )
        await callback.message.edit_text("Выберите, что изменить у ачивки:", reply_markup=kb)
    elif last.get("state") == "achievements_list":
        await show_edit_achievements_menu(callback, state)
    elif last.get("state") == "select_user_for_grant":
        # Вернуть пользователя на выбор юзера
        db = SessionLocal()
        try:
            users = db.query(User).filter(User.is_admin == False, User.tg_id != SETTINGS.admin_tg_id).all()
            if users:
                await callback.message.edit_text(
                    "👥 Выберите пользователя:",
                    reply_markup=build_users_keyboard(db, action="grant", back_action="admin_back", users=users),
                )
            else:
                await callback.message.edit_text("❌ Нет пользователей, которым можно выдать ачивку.", reply_markup=None)
                await cmd_admin_panel(callback)
                await state.clear()
        finally:
            db.close()
    elif last.get("state") == "select_user_for_revoke":
        # Вернуть пользователя на выбор юзера
        db = SessionLocal()
        try:
            users = db.query(User).filter(User.is_admin == False, User.tg_id != SETTINGS.admin_tg_id).all()
            if users:
                await callback.message.edit_text(
                    "👥 Выберите пользователя:",
                    reply_markup=build_users_keyboard(db, action="revoke", back_action="admin_back", users=users),
                )
            else:
                await callback.message.edit_text("❌ Нет пользователей, у которых можно отозвать ачивку.", reply_markup=None)
                await cmd_admin_panel(callback)
                await state.clear()
        finally:
            db.close()
    else:
        # На всякий случай возвращаем в главное меню
        await cmd_admin_panel(callback, from_back=True)
        await state.clear()

##actions

@router_admin.callback_query(UserSelectCb.filter())
@admin_required
async def process_user_selection(callback: types.CallbackQuery, callback_data: UserSelectCb, state: FSMContext):
    action = callback_data.action
    tg_id = callback_data.tg_id

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_id == tg_id).first()
        if not user:
            await callback.message.edit_text("❌ Пользователь не найден.")
            return

        # ========== ВЫДАЧА АЧИВКИ ==========
        if action == "grant":
            season = db.query(Season).filter_by(is_active=True).first()

            if not season:
                await callback.message.edit_text("❌ Нет активного сезона.")
                return

            all_achs = db.query(Achievement).filter_by(season_id=season.id).all()
            # Список кодов ачивок, которые уже есть у пользователя
            user_ids = {ua.achievement.id for ua in user.achievements}

            # Выбираем только те ачивки, которых нет у пользователя
            available = [a for a in all_achs if a.id not in user_ids]

            available = sorted(available, key=lambda a: a.title.lower())

            if not available:
                await callback.message.edit_text("ℹ У пользователя уже есть все ачивки.")
                await state.clear()
                await cmd_admin_panel(callback)
                return

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{a.title} [{'+' if a.points > 0 else ''}{a.points}]",
                            callback_data=AchievementSelectCb(
                                action="grant",
                                tg_id=user.tg_id,
                                ach_id=a.id
                            ).pack()
                        )
                    ]
                    for a in available
                ] + [[InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]]
            )

            data = await state.get_data()
            prev = data.get("prev", [])
            prev.append({"state": "select_user_for_grant"})
            await state.update_data(prev=prev)

            await callback.message.edit_text(
                f"🎖 Выберите ачивку, которую хотите выдать пользователю {user.full_name or user.tg_id}:",
                reply_markup=kb,
            )
            return

        # ========== ОТОЗВАТЬ АЧИВКУ ==========
        elif action == "revoke":
            # Список ачивок, которые есть у пользователя
            season = db.query(Season).filter_by(is_active=True).first()

            if not season:
                await callback.message.edit_text("❌ Нет активного сезона.")
                return

            user_achs = [
                ua.achievement
                for ua in user.achievements
                if ua.season_id == season.id
            ]
            if not user_achs:
                await callback.message.edit_text("ℹ У пользователя нет ачивок.")
                await state.clear()
                return await cmd_admin_panel(callback)
                

            user_achs = sorted(user_achs, key=lambda a: a.title.lower())

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{a.title} [{'+' if a.points > 0 else ''}{a.points}]",
                            callback_data=AchievementSelectCb(
                                action="revoke",
                                tg_id=user.tg_id,
                                ach_id=a.id
                            ).pack()
                        )
                    ]
                    for a in user_achs
                ] + [[InlineKeyboardButton(text="⬅ Назад", callback_data="admin_back")]]
            )

            data = await state.get_data()
            prev = data.get("prev", [])
            prev.append({"state": "select_user_for_revoke"})
            await state.update_data(prev=prev)

            await callback.message.edit_text(
                f"🧹 Выберите ачивку, которую хотите отозвать у пользователя {user.full_name or user.tg_id}:",
                reply_markup=kb,
            )
            return

        # Далее идут команды главного админа
        # Проверка, что пользователь — главный админ
        caller_id = callback.from_user.id
        if caller_id != SETTINGS.admin_tg_id:
            await callback.message.edit_text("❌ Только главный админ может выполнять это действие.")
            return

        # ========== НАЗНАЧЕНИЕ / СНЯТИЕ АДМИНКИ ==========
        if action == "make_admin":
            try:
                if user.tg_id == SETTINGS.admin_tg_id:
                    await callback.message.edit_text(f"👑 Пользователь {user.full_name or user.tg_id} уже главный админ.")
                    return
                if user.is_admin:
                    await callback.message.edit_text(f"⭐ Пользователь {user.full_name or user.tg_id} уже админ.")
                    return
                user.is_admin = True
                db.commit()
                try:
                    await set_commands_for_user(callback.message.bot, user.tg_id)
                except Exception:
                    # Пользователь удалил или заблокировал бота
                    pass
                await callback.message.edit_text(f"⭐ Пользователь {user.full_name or user.tg_id} теперь админ.")

                return
            finally:
                await cmd_admin_panel(callback)
                await state.clear()

        elif action == "remove_admin":
            try:
                if user.tg_id == SETTINGS.admin_tg_id:
                    await callback.message.edit_text(f"❌ Нельзя лишить прав главного админа.")
                    return
                if not user.is_admin:
                    await callback.message.edit_text(f"ℹ Пользователь {user.full_name or user.tg_id} и так не админ.")
                    return
                user.is_admin = False
                db.commit()
                try:
                    await set_commands_for_user(callback.message.bot, user.tg_id)
                except Exception:
                    # Пользователь удалил или заблокировал бота
                    pass
                
                if user.tg_id in MESSAGES_WITH_KB and "admin" in MESSAGES_WITH_KB[user.tg_id]:
                    old_msg_ids = MESSAGES_WITH_KB[user.tg_id]["admin"]
                    for old_id in old_msg_ids:
                        try:
                            await callback.message.bot.edit_message_reply_markup(
                                chat_id=user.tg_id,
                                message_id=old_id,
                                reply_markup=None
                            )
                        except Exception:
                            pass
                    # очищаем только админскую часть
                    MESSAGES_WITH_KB[user.tg_id]["admin"] = []

                await callback.message.edit_text(f"🚫 Пользователь {user.full_name or user.tg_id} больше не админ.")
                return
            finally:
                await cmd_admin_panel(callback)
                await state.clear()

        # ========== УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ==========
        elif action == "delete_user":
            try:
                if user.tg_id == SETTINGS.admin_tg_id:
                    await callback.message.edit_text(f"❌ Нельзя удалить главного админа.")
                    return
                db.delete(user)
                db.commit()
                await callback.message.edit_text(f"🗑 Пользователь {user.full_name or user.tg_id} удалён.")
                return
            finally:
                await cmd_admin_panel(callback)
                await state.clear()            

    finally:
        db.close()


@router_admin.callback_query(AchievementSelectCb.filter())
@admin_required
async def process_achievement_selection(callback: types.CallbackQuery, callback_data: AchievementSelectCb):
    
    action = callback_data.action
    target_user_id = callback_data.tg_id   # tg_id пользователя, если есть
    ach_id = callback_data.ach_id

    db = SessionLocal()
    try:
        svc = AchievementService(db)
        ach = db.query(Achievement).get(ach_id)

        season = svc.get_active_season()

        if not season:
            await callback.message.edit_text("❌ Нет активного сезона.")
            return

        if ach.season_id != season.id:
            await callback.message.edit_text("❌ Эта ачивка не относится к текущему сезону.")
            return
        if not ach:
            await callback.message.edit_text("❌ Ачивка не найдена.", reply_markup=None)
            return

        # Для grant/revoke нужен пользователь
        user_name = None
        if action in ("grant", "revoke"):
            user = db.query(User).filter_by(tg_id=target_user_id).first()
            if not user:
                await callback.message.edit_text("❌ Пользователь не найден.", reply_markup=None)
                return
            user_name = user.full_name or user.username or str(target_user_id)

        if action == "grant":
            try:
                svc.grant_achievement(callback.from_user.id, target_user_id, ach.id)
                admin_name = f"@{callback.from_user.username}" if callback.from_user.username else f"id={callback.from_user.id}"
                user_name_log = f"@{user.username}" if user.username else f"id={target_user_id}"
                log.log_grant_achievement(admin_name, ach.title, ach.points, ach.description, user_name_log)

                text = f"🏆 Ачивка *{ach.title}* выдана пользователю *{user_name}*."

                try:
                    await callback.bot.send_message(
                        chat_id=target_user_id,
                        text=f"🎉 Поздравляем! Вам выдана новая ачивка:\n\n🏅 *{ach.title}* \[{'+' if ach.points > 0 else ''}{pts_form(ach.points)}]\n",
                        parse_mode="Markdown"
                    )
                    await callback.message.edit_text(
                        text,
                        parse_mode="Markdown",
                        reply_markup=None
                    )
                except Exception as notify_err:
                    await callback.message.edit_text(text + f"\nНе удалось отправить уведомление пользователю {user_name}: {notify_err}",
                                                        parse_mode="Markdown", reply_markup=None)

            except AlreadyHasAchievement:
                await callback.message.edit_text("ℹ У пользователя уже есть эта ачивка.", reply_markup=None)
            except (AuthError, NotFoundError) as e:
                await callback.message.edit_text(f"❌ Ошибка: {e}", reply_markup=None)
            finally:
                await cmd_admin_panel(callback)

        elif action == "revoke":
            try:
                svc.revoke_achievement(callback.from_user.id, target_user_id, ach.id)
                admin_name = f"@{callback.from_user.username}" if callback.from_user.username else f"id={callback.from_user.id}"
                user_name_log = f"@{user.username}" if user.username else f"id={target_user_id}"
                log.log_revoke_achievement(admin_name, ach.title, ach.points, ach.description, user_name_log)

                text = f"🧹 Ачивка *{ach.title}* отозвана у пользователя *{user_name}*."

                try:
                    await callback.bot.send_message(
                        chat_id=target_user_id,
                        text=(
                            f"⚠️ У вас отозвана ачивка:\n\n🏅 *{ach.title}* \[{'+' if ach.points > 0 else ''}{pts_form(ach.points)}]\n"
                        ),
                        parse_mode="Markdown"
                    )
                    await callback.message.edit_text(
                        text,
                        parse_mode="Markdown",
                        reply_markup=None
                    )
                except Exception as notify_err:
                    await callback.message.edit_text(text + f"\nНе удалось отправить уведомление пользователю {user_name}: {notify_err}",
                                                        parse_mode="Markdown", reply_markup=None)


            except (AuthError, NotFoundError) as e:
                await callback.message.edit_text(f"❌ Ошибка: {e}", reply_markup=None)
            finally:
                await cmd_admin_panel(callback)

        elif action == "delete":
            try:
                success = svc.delete_achievement(callback.from_user.id, ach.id)
                if success:
                    admin_name = f"@{callback.from_user.username}" if callback.from_user.username else f"id={callback.from_user.id}"
                    log.log_delete_achievement(admin_name, ach.title, ach.points, ach.description)
                    await callback.message.edit_text(
                        f"🗑 Ачивка *{ach.title}* удалена из базы и у всех пользователей.",
                        parse_mode="Markdown",
                        reply_markup=None
                    )
                else:
                    await callback.message.edit_text("❌ Ачивка не найдена.", reply_markup=None)
            except AuthError:
                await callback.message.edit_text("❌ Нет прав администратора.", reply_markup=None)
            finally:
                await cmd_admin_panel(callback)

    finally:
        db.close()


# =========================
# Admin menu router
# =========================
@router_admin.callback_query(AdminActionCb.filter())
@admin_required
async def process_admin_action(callback: types.CallbackQuery, callback_data: AdminActionCb, state: FSMContext):
    await state.update_data(prev=[])
    action = callback_data.action
    db = SessionLocal()
    try:
        # ===== Пользовательские действия =====
        if action in ("grant", "revoke"):
            users = db.query(User).filter(User.is_admin == False, User.tg_id != SETTINGS.admin_tg_id).all()
            if not users:
                error_message = "❌ Нет пользователей, которым можно выдать ачивку." if action == "grant" else "❌ Нет пользователей, у которых можно отозвать ачивку."
                await callback.message.edit_text(error_message, reply_markup=None)
                await cmd_admin_panel(callback)
                await state.clear()
                return
            await callback.message.edit_text(
                f"👥 Выберите пользователя для {'выдачи' if action == 'grant' else 'лишения'} достижения:",
                reply_markup=build_users_keyboard(db, action=action, back_action="admin_back", users=users),
            )
            return
        
        elif action == "delete_user":
            users = db.query(User).filter(User.tg_id != SETTINGS.admin_tg_id).all()
            if not users:
                await callback.message.edit_text("ℹ Нет пользователей, которых можно удалить.", reply_markup=None)

                await cmd_admin_panel(callback)
                await state.clear()

                return
            await callback.message.edit_text(
                "👥 Выберите пользователя для удаления:",
                reply_markup=build_users_keyboard(db, action=action, back_action="admin_back", users=users)
            )
            return
        
        elif action == "make_admin":
            users = db.query(User).filter(User.is_admin == False, User.tg_id != SETTINGS.admin_tg_id).all()
            if not users:
                await callback.message.edit_text("ℹ Нет пользователей, которых можно сделать админом.", reply_markup=None)

                await cmd_admin_panel(callback)
                await state.clear()

                return
            await callback.message.edit_text(
                "👥 Выберите пользователя для назначения админом:",
                reply_markup=build_users_keyboard(db, action=action, back_action="admin_back", users=users)
            )
            return
        
        elif action == "remove_admin":
            users = db.query(User).filter(User.is_admin == True, User.tg_id != SETTINGS.admin_tg_id).all()
            if not users:
                await callback.message.edit_text("ℹ Нет админов, которых можно лишить прав.", reply_markup=None)

                await cmd_admin_panel(callback)
                await state.clear()

                return
            await callback.message.edit_text(
                "👥 Выберите админа для снятия прав:",
                reply_markup=build_users_keyboard(db, action=action, back_action="admin_back", users=users)
            )
            return


        # ===== Просмотр пользователей =====
        elif action == "users":
            users = db.query(User).all()
            if not users:
                await callback.message.edit_text("❌ Пользователей нет.", reply_markup=None)
                return
            users = sorted(users, key=lambda u: (u.full_name or u.username or str(u.tg_id)).lower())
            lines = ["📋 Список пользователей:"]
            for u in users:
                role = "⭐ Админ" if u.is_admin else "👤 Пользователь"
                tg_display = f"@{u.username}" if u.username else str(u.tg_id)
                if u.tg_id == SETTINGS.admin_tg_id:
                    role = "👑 Главный админ"
                lines.append(f"\n{u.full_name or 'Без имени'} — {tg_display} ({role})")
            return await callback.message.edit_text("".join(lines), reply_markup=back_to_previous_kb())
            

        # ===== Создание ачивки =====
        elif action == "add_achievement":
            await add_achievement_start(callback, state)

        # ===== Удаление ачивки =====
        elif action == "delete_achievement":
            season = db.query(Season).filter_by(is_active=True).first()

            if not season:
                await callback.message.answer("❌ Активный сезон не найден.")
                return

            achs_count = db.query(Achievement).filter_by(season_id=season.id).count()
            if not achs_count:
                await callback.message.edit_text("❌ Ачивок нет.", reply_markup=None)

                await cmd_admin_panel(callback)
                await state.clear()

                return
            await callback.message.edit_text(
                "🗑 Выберите ачивку для удаления:",
                reply_markup=build_achievements_keyboard(db, action="delete", tg_id=0),
            )
            return

        # ===== Редактирование ачивки =====
        elif action == "edit_achievement":
            await show_edit_achievements_menu(callback, state)

        elif action == "logs":
            # Проверка на главного админа
            if callback.from_user.id != SETTINGS.admin_tg_id:
                await callback.answer("❌ Только главный админ может выполнять это действие.")
                return

            log_path = SETTINGS.logs_path
            if os.path.exists(log_path) and os.path.getsize(SETTINGS.logs_path) > 0:
                file = FSInputFile(SETTINGS.logs_path)
                media = InputMediaDocument(media=file, caption="📄 Лог-файл действий админов")
                await callback.message.edit_media(media)
            else:
                await callback.message.edit_text("⚠ Логи пока не созданы.")
            await cmd_admin_panel(callback)
            await state.clear()
            return
            

        elif action == "attendance":
            await handle_attendance(callback, state)



    finally:
        db.close()
