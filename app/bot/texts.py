"""Bot matnlari (o'zbek lotin va kirill).

Standart matnlar shu yerda (LAT). Admin istalgan matnni web paneldagi
«Bot matnlari» sahifasida o'zgartirishi mumkin — o'zgartirilgani bazada
'txt.<kalit>' (lotin) va 'txt.<kalit>.cyr' (kirill) sozlamasi sifatida saqlanadi.

Kirill varianti berilmasa, lotin matn avtomatik o'giriladi.
"""
from __future__ import annotations

import re
from functools import lru_cache

from aiogram.filters import Filter
from aiogram.types import Message

from ..services import settings as st
from ..utils import lat2cyr

LAT: dict[str, str] = {
    # --- Til ---
    "choose_lang": "🌐 Tilni tanlang / Тилни танланг: 👇",
    "btn_lat": "🇺🇿 O'zbekcha (lotin)",
    "btn_cyr": "🇺🇿 Ўзбекча (кирилл)",
    "lang_changed": "✅ Til o'zgartirildi! 🌐",

    # --- Muddat ---
    "reg_not_started": "⏳ Ro'yxatdan o'tish hali boshlanmagan.\n🗓 Boshlanish vaqti: <b>{start}</b>\n\n"
                       "🔔 Shu vaqtda qayta /start bosing!",
    "reg_closed": "⛔️ Ro'yxatdan o'tish yakunlangan.\n🙏 Qiziqishingiz uchun rahmat!",
    "no_setup": "🛠 Ro'yxatdan o'tish hali sozlanmoqda.\n⏳ Birozdan so'ng qayta urinib ko'ring.",

    # --- Obuna ---
    "subscribe": "📢 Ro'yxatdan o'tish uchun quyidagi kanallarga <b>obuna bo'ling</b> 👇\n\n"
                 "✅ So'ng «Obuna bo'ldim — tekshirish» tugmasini bosing.",
    "btn_check": "✅ Obuna bo'ldim — tekshirish",
    "not_subscribed": "🙈 Siz hali quyidagi kanal(lar)ga obuna bo'lmagansiz:\n{list}\n\n"
                      "Obuna bo'lib, qayta tekshiring.",
    "subscribed_ok": "🥳 Rahmat! Obuna tasdiqlandi.",
    "lost_subscription": "😕 Siz kanal(lar)dan chiqib ketgansiz. Ro'yxatdan o'tishni yakunlash uchun "
                         "qayta obuna bo'ling 👇",
    "ask_instagram": "📸 Instagram sahifangiz <b>username</b>'ini yozing.\n\n"
                     "✏️ Namuna: <code>aziz_aliyev</code> yoki <code>@aziz_aliyev</code>",
    "bad_instagram": "❗️ Username noto'g'ri. Faqat lotin harflari, raqamlar, nuqta va pastki chiziq (_) "
                     "bo'lishi mumkin, 30 belgigacha.\n✏️ Namuna: <code>aziz_aliyev</code>",

    # --- Nizom ---
    "rules_intro": "📜 <b>Olimpiada nizomi</b>\n\n🧐 Iltimos, nizom bilan diqqat bilan tanishib chiqing. "
                   "So'ng eng pastdagi tugmani bosing 👇",
    "rules_confirm": "🤔 Nizom bilan to'liq tanishib chiqdingizmi?",
    "btn_rules_ok": "✅ Tanishib chiqdim va tushundim",

    # --- Ro'yxatdan o'tish savollari ---
    "ask_first_name": "👤 <b>Ismingizni</b> yozing.\n\n✏️ Namuna: <code>{example}</code>",
    "ask_last_name": "👥 <b>Familiyangizni</b> yozing.\n\n✏️ Namuna: <code>{example}</code>",
    "bad_name": "🙅 Noto'g'ri kiritildi. Faqat harflardan foydalaning (2–40 belgi), raqam va "
                "belgilar qo'shmang.\n✏️ Namuna: <code>{example}</code>",
    "ex_first_name": "Aziz",
    "ex_last_name": "Aliyev",
    "ask_phone": "📱 <b>Telefon raqamingizni</b> yuboring.\n\n"
                 "👇 «📱 Raqamni yuborish» tugmasini bosing\n"
                 "✍️ yoki qo'lda yozing: <code>+998901234567</code> yoki <code>90 123 45 67</code>",
    "btn_send_contact": "📱 Raqamni yuborish",
    "bad_phone": "📵 Telefon raqam noto'g'ri. O'zbekiston raqamini kiriting.\n"
                 "✏️ Namuna: <code>+998901234567</code> yoki <code>901234567</code>",
    "foreign_contact": "🙅 Iltimos, o'zingizning raqamingizni yuboring.",
    "phone_taken": "⚠️ Bu telefon raqam bilan boshqa o'quvchi ro'yxatdan o'tgan. Bitta raqam bilan "
                   "faqat bitta o'quvchi ro'yxatdan o'tishi mumkin. 📱 Boshqa raqam kiriting.",
    "ask_school": "🏫 <b>Maktabingizni</b> tanlang 👇\n\n🔎 Topish oson bo'lishi uchun maktab raqami yoki "
                  "nomini yozib yuborishingiz ham mumkin. Namuna: <code>5</code>",
    "school_not_found": "🤷 «{q}» bo'yicha maktab topilmadi. Ro'yxatdan tanlang yoki boshqacha yozib ko'ring.",
    "school_found": "🔎 Topilgan maktablar — keraklisini tanlang 👇",
    "choose_from_list": "👇 Iltimos, tugmalardan birini tanlang.",
    "ask_address": "🏠 <b>Yashash manzilingizni</b> yozing.\n\n"
                   "✏️ Namuna: <code>Farg'ona viloyati, Qo'qon shahri, Navbahor ko'chasi 12-uy</code>",
    "bad_address": "❗️ Manzil noto'g'ri (5–200 belgi bo'lishi kerak). Viloyat, tuman/shahar, "
                   "mahalla yoki ko'cha va uyni yozing.\n"
                   "✏️ Namuna: <code>Farg'ona viloyati, Qo'qon shahri, Navbahor ko'chasi 12-uy</code>",
    "ask_grade": "🎒 <b>Sinfingizni</b> tanlang 👇",
    "ask_subjects": "📚 <b>Qatnashadigan fan(lar)ingizni</b> tanlang 👇\n\n"
                    "☑️ Tanlagach «Davom etish ➡️» tugmasini bosing.",
    "subjects_max_hint": "\n☝️ Ko'pi bilan <b>{max}</b> ta fan tanlash mumkin.",
    "no_subjects_selected": "☝️ Kamida bitta fan tanlang.",
    "max_subjects": "✋ Ko'pi bilan {max} ta fan tanlash mumkin.",
    "no_subjects_for_grade": "😔 Sizning sinfingiz uchun hozircha fan mavjud emas. Boshqa sinfni "
                             "tanlang yoki tashkilotchilarga murojaat qiling.",
    "subjects_reset": "🔄 Sinf o'zgargani uchun fanlarni qaytadan tanlang.",
    "btn_continue": "Davom etish ➡️",
    "btn_done": "✅ Tayyor",
    "btn_skip": "⏭ O'tkazib yuborish",
    "example": "✏️ Namuna: <code>{example}</code>",
    "optional_hint": "<i>(majburiy emas)</i>",
    "bad_text_len": "📏 Javob {min} tadan {max} tagacha belgidan iborat bo'lishi kerak.",
    "bad_number": "🔢 Iltimos, faqat son kiriting.",
    "bad_number_range": "🔢 Son {min} dan {max} gacha bo'lishi kerak.",
    "choose_at_least_one": "☝️ Kamida bitta variantni tanlang.",

    # --- Tasdiqlash ---
    "confirm": "📋 <b>Ma'lumotlaringizni tekshiring:</b>\n\n{summary}\n\n🧐 Hammasi to'g'rimi?",
    "btn_confirm": "✅ Tasdiqlash",
    "btn_edit": "✏️ Tahrirlash",
    "edit_choose": "✏️ Qaysi ma'lumotni o'zgartirasiz? 👇",
    "f_first_name": "Ism",
    "f_last_name": "Familiya",
    "f_phone": "Telefon",
    "f_school": "Maktab",
    "f_address": "Manzil",
    "f_grade": "Sinf",
    "f_subjects": "Fanlar",
    "f_instagram": "Instagram",
    "f_code": "ID raqam",
    "saved_code": "🆔 Sizning ishtirokchi raqamingiz: <b>{code}</b>\n📌 Uni saqlab qo'ying!",
    "updated": "✅ Ma'lumotlaringiz yangilandi! 👍",
    "btn_back": "⬅️ Orqaga",
    "btn_cancel": "❌ Bekor qilish",
    "cancelled": "🛑 Ro'yxatdan o'tish to'xtatildi.\n🔁 Qaytadan boshlash uchun /start ni bosing.",
    "edit_cancelled": "↩️ Tahrirlash bekor qilindi.",

    # --- Menyu ---
    "already": "✅ Siz ro'yxatdan o'tgansiz! 😊\n👇 Quyidagi menyudan foydalaning.",
    "btn_profile": "👤 Ma'lumotlarim",
    "btn_results": "📊 Natijalarim",
    "btn_cert": "🎓 Sertifikat",
    "btn_lang": "🌐 Til",
    "btn_edit_profile": "✏️ Ma'lumotni o'zgartirish",
    "profile": "👤 <b>Sizning ma'lumotlaringiz</b>\n\n{summary}",
    "use_menu": "👇 Iltimos, menyudagi tugmalardan foydalaning.",
    "edit_closed": "🔒 Ma'lumotni o'zgartirib bo'lmaydi: muddat tugagan yoki natijalaringiz kiritilgan. "
                   "Tashkilotchilarga murojaat qiling.",
    "disqualified": "⛔️ Siz olimpiadadan chetlatilgansiz. Savollar bo'lsa tashkilotchilarga murojaat qiling.",

    # --- Natijalar ---
    "results_not_published": "⏳ Natijalar hali e'lon qilinmagan.\n🔔 E'lon qilinganda shu yerga xabar beramiz!",
    "results_header": "🏆 <b>{olympiad}</b>\n📊 <b>{round}</b> natijalari\n👤 {name} (ID: {code})",
    "result_line": "📘 <b>{subject}</b>\n🎯 Ball: <b>{score}</b> / {max} ({percent}%)\n"
                   "🏅 O'rin: <b>{place}</b> — {scope} ({count} ishtirokchi)",
    "result_missing": "📘 <b>{subject}</b>: ⏳ natija hali kiritilmagan",
    "block_line": "🧩 <b>{block}</b> (blok)\n🎯 Jami: <b>{score}</b> / {max} ({percent}%)\n"
                  "🏅 O'rin: <b>{place}</b> — {scope} ({count} ishtirokchi)",
    "scope_grade": "{grade}-sinflar orasida",
    "scope_all": "umumiy",
    "results_none": "🤷 Sizda hali natija yo'q.",
    "results_ready": "📣 Natijalar e'lon qilindi! 🎉",

    # --- Sertifikat ---
    "cert_not_ready": "⏳ Sertifikatlar hali tayyor emas.",
    "cert_none": "🤷 Sizga tegishli sertifikat topilmadi.",
    "cert_preparing": "⏳ Sertifikat tayyorlanmoqda... 🖨",
    "cert_caption": "🎓 {title}",

    # --- E'tiroz (apellyatsiya) ---
    "btn_appeal": "⚖️ E'tiroz",
    "appeal_closed": "⏳ Hozir e'tiroz qabul qilinmayapti.",
    "appeal_choose": "⚖️ Qaysi fan natijasiga e'tiroz bildirasiz? 👇",
    "appeal_none": "🤷 Sizda e'tiroz bildirish mumkin bo'lgan natija yo'q.",
    "appeal_exists": "ℹ️ Bu fan bo'yicha e'tirozingiz allaqachon qabul qilingan. Javobni kuting 🙏",
    "appeal_ask": "✍️ <b>{subject}</b> fani bo'yicha e'tirozingizni batafsil yozing: qaysi savol va nima uchun.\n\n"
                  "📏 10–1000 belgi.",
    "appeal_bad_len": "📏 E'tiroz 10 tadan 1000 tagacha belgidan iborat bo'lishi kerak. Qaytadan yozing.",
    "appeal_saved": "✅ E'tirozingiz qabul qilindi (№{id}).\n🔔 Ko'rib chiqilgach shu yerga javob yuboramiz.",
    "appeal_answer": "⚖️ <b>E'tirozingiz ko'rib chiqildi</b> (№{id}, {subject})\n\n"
                     "Qaror: <b>{status}</b>\n💬 {answer}",
    "appeal_accepted": "✅ qabul qilindi",
    "appeal_rejected": "❌ rad etildi",
    "appeal_pending": "⏳ ko'rib chiqilmoqda",
    "appeal_cancelled": "↩️ E'tiroz bekor qilindi.",
    "appeal_my": "📄 <b>Sizning e'tirozlaringiz:</b>\n{list}",

    "error": "⚠️ Xatolik yuz berdi. 🔁 Iltimos, qaytadan urinib ko'ring yoki /start ni bosing.",
}

# Kirillga avtomatik o'girish mos kelmaydigan matnlar
CYR_OVERRIDES: dict[str, str] = {
    "choose_lang": LAT["choose_lang"],
    "btn_lat": LAT["btn_lat"],
    "btn_cyr": LAT["btn_cyr"],
    "ask_instagram": "📸 Instagram саҳифангиз <b>username</b>'ини ёзинг (лотин ҳарфларида).\n\n"
                     "✏️ Намуна: <code>aziz_aliyev</code> ёки <code>@aziz_aliyev</code>",
    "bad_instagram": "❗️ Username нотўғри. Фақат лотин ҳарфлари, рақамлар, нуқта ва пастки чизиқ (_) "
                     "бўлиши мумкин, 30 белгигача.\n✏️ Намуна: <code>aziz_aliyev</code>",
    "f_instagram": "Instagram",
}

# ---------------------------------------------------------------------------
# Admin panel uchun: matnlar guruhlari va tavsifi
# ---------------------------------------------------------------------------
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("🌐 Til tanlash", [
        ("choose_lang", "Til tanlash so'rovi (ikkala tilda bir xil ko'rinadi)"),
        ("btn_lat", "Tugma: lotin"), ("btn_cyr", "Tugma: kirill"),
        ("lang_changed", "Til o'zgargach"),
    ]),
    ("⏳ Muddat", [
        ("reg_not_started", "Ro'yxat hali boshlanmagan"), ("reg_closed", "Ro'yxat yopilgan"),
        ("no_setup", "Maktab/sinf/fan kiritilmagan bo'lsa"),
    ]),
    ("📢 Obuna va Instagram", [
        ("subscribe", "Obuna bo'lish so'rovi"), ("btn_check", "Tugma: tekshirish"),
        ("not_subscribed", "Obuna bo'lmagan (oyna, 200 belgigacha)"), ("subscribed_ok", "Obuna tasdiqlandi"),
        ("lost_subscription", "Kanaldan chiqib ketgan"),
        ("ask_instagram", "Instagram username so'rovi"), ("bad_instagram", "Username noto'g'ri"),
    ]),
    ("📜 Nizom", [
        ("rules_intro", "Nizomdan oldingi xabar"), ("rules_confirm", "Nizom oxiridagi savol"),
        ("btn_rules_ok", "Tugma: tushundim"),
    ]),
    ("👤 Ism va familiya", [
        ("ask_first_name", "Ism so'rovi"), ("ex_first_name", "Ism namunasi"),
        ("ask_last_name", "Familiya so'rovi"), ("ex_last_name", "Familiya namunasi"),
        ("bad_name", "Ism/familiya noto'g'ri"),
    ]),
    ("📱 Telefon", [
        ("ask_phone", "Telefon so'rovi"), ("btn_send_contact", "Tugma: raqamni yuborish"),
        ("bad_phone", "Raqam noto'g'ri"), ("foreign_contact", "Boshqa odamning kontakti yuborilgan"),
        ("phone_taken", "Raqam band"),
    ]),
    ("🏫 Maktab va manzil", [
        ("ask_school", "Maktab so'rovi"), ("school_not_found", "Qidiruvda topilmadi"),
        ("school_found", "Qidiruv natijasi"), ("ask_address", "Manzil so'rovi"),
        ("bad_address", "Manzil noto'g'ri"),
    ]),
    ("🎒 Sinf va fanlar", [
        ("ask_grade", "Sinf so'rovi"), ("ask_subjects", "Fan tanlash so'rovi"),
        ("subjects_max_hint", "Fanlar soni chegarasi (izoh)"), ("no_subjects_selected", "Fan tanlanmagan"),
        ("max_subjects", "Ko'p fan tanlangan"), ("no_subjects_for_grade", "Sinf uchun fan yo'q"),
        ("subjects_reset", "Sinf o'zgargach"), ("btn_continue", "Tugma: davom etish"),
    ]),
    ("❓ Qo'shimcha savollar", [
        ("example", "Namuna qatori"), ("optional_hint", "«Majburiy emas» belgisi"),
        ("btn_done", "Tugma: tayyor"), ("btn_skip", "Tugma: o'tkazib yuborish"),
        ("bad_text_len", "Javob uzunligi noto'g'ri"), ("bad_number", "Son emas"),
        ("bad_number_range", "Son oraliqdan tashqarida"), ("choose_at_least_one", "Variant tanlanmagan"),
        ("choose_from_list", "Tugma o'rniga matn yozilgan"),
    ]),
    ("📋 Tasdiqlash va tahrirlash", [
        ("confirm", "Ma'lumotlarni tekshirish"), ("btn_confirm", "Tugma: tasdiqlash"),
        ("btn_edit", "Tugma: tahrirlash"), ("edit_choose", "Qaysi maydonni o'zgartirish"),
        ("saved_code", "ID raqam xabari"), ("updated", "Ma'lumot yangilandi"),
        ("btn_back", "Tugma: orqaga"), ("btn_cancel", "Tugma: bekor qilish"),
        ("cancelled", "Ro'yxatdan o'tish bekor qilindi"), ("edit_cancelled", "Tahrirlash bekor qilindi"),
        ("f_first_name", "Maydon nomi: ism"), ("f_last_name", "Maydon nomi: familiya"),
        ("f_phone", "Maydon nomi: telefon"), ("f_school", "Maydon nomi: maktab"),
        ("f_address", "Maydon nomi: manzil"), ("f_grade", "Maydon nomi: sinf"),
        ("f_subjects", "Maydon nomi: fanlar"), ("f_instagram", "Maydon nomi: Instagram"),
        ("f_code", "Maydon nomi: ID"),
    ]),
    ("🏠 Menyu", [
        ("already", "Ro'yxatdan o'tgan foydalanuvchi /start bossa"),
        ("btn_profile", "Tugma: ma'lumotlarim"), ("btn_results", "Tugma: natijalarim"),
        ("btn_cert", "Tugma: sertifikat"), ("btn_lang", "Tugma: til"),
        ("btn_edit_profile", "Tugma: ma'lumotni o'zgartirish"), ("profile", "Ma'lumotlarim"),
        ("use_menu", "Tushunarsiz xabar yozilganda"), ("edit_closed", "O'zgartirish yopiq"),
        ("disqualified", "Chetlatilgan"),
    ]),
    ("📊 Natijalar va sertifikat", [
        ("results_ready", "Natijalar e'lon qilindi (ommaviy xabar sarlavhasi)"),
        ("results_not_published", "Natijalar hali yo'q"), ("results_header", "Natijalar sarlavhasi"),
        ("result_line", "Fan natijasi"), ("result_missing", "Fan natijasi kiritilmagan"),
        ("block_line", "Blok natijasi"), ("scope_grade", "«Sinflar orasida»"), ("scope_all", "«Umumiy»"),
        ("results_none", "Natija yo'q"), ("cert_not_ready", "Sertifikat tayyor emas"),
        ("cert_none", "Sertifikat yo'q"), ("cert_preparing", "Sertifikat tayyorlanmoqda"),
        ("cert_caption", "Sertifikat izohi"),
    ]),
    ("⚖️ E'tirozlar", [
        ("btn_appeal", "Tugma: e'tiroz (menyuda, e'tirozlar ochiq bo'lsa)"),
        ("appeal_closed", "E'tiroz yopiq"), ("appeal_choose", "Fan tanlash"),
        ("appeal_none", "Natija yo'q"), ("appeal_exists", "Bu fan bo'yicha e'tiroz bor"),
        ("appeal_ask", "E'tiroz matnini so'rash"), ("appeal_bad_len", "Matn uzunligi noto'g'ri"),
        ("appeal_saved", "E'tiroz qabul qilindi"), ("appeal_answer", "Admin javobi"),
        ("appeal_accepted", "«Qabul qilindi»"), ("appeal_rejected", "«Rad etildi»"),
        ("appeal_pending", "«Ko'rib chiqilmoqda»"), ("appeal_my", "Mening e'tirozlarim"),
        ("appeal_cancelled", "E'tiroz bekor qilindi"),
    ]),
    ("⚠️ Boshqa", [("error", "Kutilmagan xato")]),
]

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def kind(key: str) -> str:
    """'button' — tugma; 'plain' — HTML'siz qisqa matn (namuna, maydon nomi); 'html' — xabar."""
    if key.startswith("btn_"):
        return "button"
    if key.startswith(("ex_", "f_", "scope_")) or key in ("appeal_accepted", "appeal_rejected", "appeal_pending"):
        return "plain"
    return "html"


def placeholders(key: str) -> set[str]:
    """Standart matndagi {o'zgaruvchi}lar — admin matnida ham bo'lishi shart."""
    return set(_PLACEHOLDER.findall(LAT.get(key, "")))


@lru_cache(maxsize=2048)
def _to_cyr(text: str) -> str:
    return lat2cyr(text)


def default(key: str, lang: str = "lat") -> str:
    """O'zgartirilmagan (standart) matn."""
    if lang == "cyr":
        return CYR_OVERRIDES.get(key) or _to_cyr(LAT.get(key, key))
    return LAT.get(key, key)


def t(key: str, lang: str = "lat") -> str:
    """Matnni tilga qarab qaytaradi (admin o'zgartirgan bo'lsa — o'sha)."""
    custom = st.raw(f"txt.{key}")
    if lang == "cyr":
        custom_cyr = st.raw(f"txt.{key}.cyr")
        if custom_cyr:
            return custom_cyr
        if custom:
            # choose_lang kabi ikki tilli matnlar o'girilmaydi
            return custom if key in ("choose_lang", "btn_lat", "btn_cyr") else _to_cyr(custom)
        return default(key, "cyr")
    return custom or LAT.get(key, key)


def both(key: str) -> set[str]:
    """Tugma matnining barcha variantlari (joriy + standart, ikkala tilda).

    Standart variant ham qo'shiladi: admin tugmani o'zgartirganda eski klaviaturasi
    ochiq qolgan foydalanuvchilarning bosishi ham ishlayveradi.
    """
    return {t(key, "lat"), t(key, "cyr"), default(key, "lat"), default(key, "cyr")}


class Btn(Filter):
    """Xabar matni ko'rsatilgan tugma(lar)dan biriga tengmi — har safar joriy matn bilan tekshiradi."""

    def __init__(self, *keys: str) -> None:
        self.keys = keys

    async def __call__(self, message: Message) -> bool:
        text = message.text
        return bool(text) and any(text in both(k) for k in self.keys)
