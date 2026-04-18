from aiogram.fsm.state import StatesGroup, State

class RegisterForm(StatesGroup):
    waiting_for_full_name = State()