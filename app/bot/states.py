"""Bot holatlari (FSM)."""
from aiogram.fsm.state import State, StatesGroup


class Reg(StatesGroup):
    subscribe = State()   # kanallarga obuna
    instagram = State()   # instagram username (admin yoqqan bo'lsa)
    rules = State()       # nizom bilan tanishish
    first_name = State()
    last_name = State()
    phone = State()
    school = State()
    address = State()
    grade = State()
    subjects = State()
    custom = State()      # admin qo'shgan savollar
    confirm = State()
    edit_menu = State()


class AppealSt(StatesGroup):
    text = State()        # e'tiroz matni


# Ma'lumot kiritish bosqichlari tartibi
STEPS = ["first_name", "last_name", "phone", "school", "address", "grade", "subjects", "custom", "confirm"]
