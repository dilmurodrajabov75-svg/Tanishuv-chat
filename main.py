"""
Tanishuv boti (aiogram 3.x): anonim suhbat + VIP + admin panel.
requirements.txt:  aiogram
"""
import asyncio
import html
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

import aiohttp
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# =================== SOZLAMALAR (faqat shu yerni to'ldiring) ===================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BU_YERGA_YANGI_TOKENNI_YOZING")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8554402317"))
# ===============================================================================

# ---------------------------------------------------------------- DB
db = sqlite3.connect("dating.db")
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, gender TEXT, age INTEGER, city TEXT,
  photo TEXT, username TEXT, vip_until INTEGER DEFAULT 0,
  banned INTEGER DEFAULT 0, plan_chosen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS queue(user_id INTEGER PRIMARY KEY, gender TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS pairs(a INTEGER, b INTEGER);
CREATE TABLE IF NOT EXISTS watch(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
INSERT OR IGNORE INTO settings VALUES('card','Karta raqami kiritilmagan');
INSERT OR IGNORE INTO settings VALUES('price','50 000');
INSERT OR IGNORE INTO settings VALUES('vip_days','30');
INSERT OR IGNORE INTO settings VALUES('channel','');
INSERT OR IGNORE INTO settings VALUES('photo_secs','5');
INSERT OR IGNORE INTO settings VALUES('intro','');
CREATE TABLE IF NOT EXISTS reports(reporter INTEGER, reported INTEGER, ts INTEGER);
""")
db.commit()
try:
    db.execute("ALTER TABLE users ADD COLUMN created INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass


def q(sql, args=(), one=False, commit=False):
    cur = db.execute(sql, args)
    if commit:
        db.commit()
        return cur.lastrowid
    return cur.fetchone() if one else cur.fetchall()


def setting(k):
    return q("SELECT v FROM settings WHERE k=?", (k,), one=True)["v"]


def esc(s):
    return html.escape(str(s))


def get_user(uid):
    return q("SELECT * FROM users WHERE id=?", (uid,), one=True)


def is_vip(uid):
    u = get_user(uid)
    return bool(u and u["vip_until"] > time.time())


def partner(uid):
    r = q("SELECT * FROM pairs WHERE a=? OR b=?", (uid, uid), one=True)
    if not r:
        return None
    return r["b"] if r["a"] == uid else r["a"]


def today():
    return datetime.utcnow().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- Taqiqlangan kontaktlar
PHONE_RE = re.compile(r"(?:\d[\s\-\.\(\)]*){9,}")
HANDLE_RE = re.compile(r"@\w{3,}")
LINK_RE = re.compile(r"(https?://|www\.|t\.me|telegram\.me|wa\.me|instagram\.com|\.com\b|\.uz\b)", re.I)
APP_RE = re.compile(
    r"(telegram|telegramm|tg\b|insta|instagram|whatsapp|vatsap|watsap|snapchat|snap\b|tiktok|"
    r"facebook|viber|imo\b|телеграм|инстаграм|инста|ватсап|вацап|тикток)", re.I)


def has_contact(text):
    return bool(PHONE_RE.search(text) or HANDLE_RE.search(text)
                or LINK_RE.search(text) or APP_RE.search(text))


# ---------------------------------------------------------------- States
class Reg(StatesGroup):
    name = State(); gender = State(); age = State(); city = State(); photo = State()


class Pay(StatesGroup):
    receipt = State()


class Adm(StatesGroup):
    setting = State(); broadcast = State(); ban = State(); unban = State()
    watch = State(); grant = State(); find = State(); msg = State()


router = Router()


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u and u.id != ADMIN_ID:
            row = get_user(u.id)
            if row and row["banned"]:
                return
        return await handler(event, data)


# ---------------------------------------------------------------- Klaviaturalar
def main_menu(uid):
    b = ReplyKeyboardBuilder()
    for t in ("💬 Suhbatdosh topish", "👤 Profilim", "💎 VIP obuna", "ℹ️ Yordam"):
        b.button(text=t)
    if uid == ADMIN_ID:
        b.button(text="🛠 Admin panel")
    b.adjust(2, 2, 1)
    return b.as_markup(resize_keyboard=True)


def chat_kb():
    b = ReplyKeyboardBuilder()
    for t in ("⏭ Keyingisi", "⛔ Tugatish", "🖼 Rasmini ko'rish", "🔍 Kim bilan?", "⚠️ Shikoyat"):
        b.button(text=t)
    b.adjust(2, 2, 1)
    return b.as_markup(resize_keyboard=True)


def cancel_kb(text="❌ Bekor qilish"):
    b = ReplyKeyboardBuilder()
    b.button(text=text)
    return b.as_markup(resize_keyboard=True)


def inline(rows, width=2):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.adjust(width)
    return b.as_markup()


RULES = ("📜 <b>Qoidalar</b>\n\n"
         "• Bot faqat <b>18 yosh va undan katta</b> foydalanuvchilar uchun.\n"
         "• Haqorat, tahdid va noqonuniy xatti-harakatlar taqiqlanadi.\n"
         "• Xavfsizlik maqsadida suhbatlar admin tomonidan kuzatilishi mumkin.\n"
         "• Tekin obunada telefon raqam, nik va havolalar yuborish taqiqlanadi.\n\n"
         "Davom etib, shu qoidalarga rozilik bildirasiz.")


INTRO = ("👋 <b>Botga xush kelibsiz!</b>\n\n"
         "Bu bot orqali yangi odamlar bilan <b>anonim</b> suhbatlashishingiz mumkin.\n\n"
         "<b>Qanday ishlaydi:</b>\n"
         "1️⃣ Qisqa ro'yxatdan o'tasiz (ism, jins, yosh, shahar, rasm).\n"
         "2️⃣ «💬 Suhbatdosh topish» ni bosasiz, bot qarama-qarshi jinsdan suhbatdosh topadi.\n"
         "3️⃣ Suhbat bot orqali o'tadi, bir-biringizning Telegram manzilingizni ko'rmaysiz.\n"
         "4️⃣ «⏭ Keyingisi» bilan boshqasini topasiz, «⛔ Tugatish» bilan tugatasiz.\n\n"
         "🆓 <b>Tekin tarif:</b> matn, stiker va ovozli xabar yuborasiz. Telefon raqam, nik va havola "
         "yuborish taqiqlangan. Suhbatdosh rasmi {secs} soniya ko'rinadi.\n"
         "💎 <b>VIP:</b> cheklov yo'q, suhbatdosh kimligi va Telegram manzilini ko'rasiz, rasm doim ochiq.\n\n"
         "⚠️ Xavfsizlik uchun suhbatlar admin tomonidan kuzatilishi mumkin.")


def intro_text():
    custom = setting("intro").strip()
    if custom:
        return esc(custom)
    return INTRO.replace("{secs}", setting("photo_secs"))


def vip_text():
    return ("💎 <b>VIP obuna</b>\n\n"
            "✅ Suhbatdosh kim ekanini bilish va uning Telegram manzilini olish\n"
            "✅ Telefon raqam, nik va havolalar yuborish\n"
            "✅ Suhbatdosh rasmini cheklovsiz ko'rish\n"
            "✅ Rasm, video va fayllar yuborish\n\n"
            f"💵 Narxi: <b>{esc(setting('price'))} so'm</b> / {esc(setting('vip_days'))} kun\n"
            f"💳 Karta: <code>{esc(setting('card'))}</code>\n\n"
            "To'lov qilgach «📸 Chek yuborish» tugmasini bosing.")


def vip_kb():
    return inline([("📸 Chek yuborish", "vip:receipt")], 1)


# ---------------------------------------------------------------- Obuna / reja
async def check_sub(bot, uid):
    ch = setting("channel").strip()
    if not ch or uid == ADMIN_ID:
        return True
    try:
        m = await bot.get_chat_member(ch, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.warning("Obuna tekshirib bo'lmadi: %s", e)
        return True


async def gate(m, bot):
    """Ro'yxatdan o'tgan va obuna bo'lganini tekshiradi. True = o'tdi."""
    uid = m.chat.id
    if not get_user(uid):
        await m.answer("Avval /start bosib ro'yxatdan o'ting.")
        return False
    if not await check_sub(bot, uid):
        ch = setting("channel").strip()
        kb = InlineKeyboardBuilder()
        if ch.startswith("@"):
            kb.button(text="📢 Obuna bo'lish", url="https://t.me/" + ch[1:])
        kb.button(text="✅ Tekshirish", callback_data="sub:check")
        kb.adjust(1)
        await m.answer("Botdan foydalanish uchun avval kanal/guruhga obuna bo'ling 👇",
                       reply_markup=kb.as_markup())
        return False
    u = get_user(uid)
    if not u["plan_chosen"]:
        await m.answer("Tarifni tanlang:", reply_markup=inline(
            [("🆓 Tekin", "plan:free"), ("💎 VIP", "plan:vip")]))
        return False
    return True


@router.callback_query(F.data == "sub:check")
async def sub_check(c: CallbackQuery, bot: Bot):
    if await check_sub(bot, c.from_user.id):
        await c.message.delete()
        if await gate(c.message, bot):
            await c.message.answer("Menyu:", reply_markup=main_menu(c.from_user.id))
    else:
        await c.answer("Hali obuna bo'lmagansiz!", show_alert=True)


@router.callback_query(F.data.startswith("plan:"))
async def plan_choose(c: CallbackQuery):
    q("UPDATE users SET plan_chosen=1 WHERE id=?", (c.from_user.id,), commit=True)
    await c.message.delete()
    if c.data == "plan:vip":
        await c.message.answer(vip_text(), reply_markup=vip_kb())
    await c.message.answer("Menyu:", reply_markup=main_menu(c.from_user.id))


# ---------------------------------------------------------------- START / RO'YXAT
@router.message(F.text == "❌ Bekor qilish")
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=main_menu(m.from_user.id))


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = m.from_user.id
    if not get_user(uid):
        await m.answer(intro_text(), reply_markup=ReplyKeyboardRemove())
        await m.answer(RULES)
        await m.answer("✏️ Ismingizni yozing:")
        await state.set_state(Reg.name)
        return
    if partner(uid):
        return await m.answer("Siz hozir suhbatdasiz.", reply_markup=chat_kb())
    if await gate(m, bot):
        await m.answer("Asosiy menyu:", reply_markup=main_menu(uid))


@router.message(Reg.name, F.text)
async def reg_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip()[:30])
    await m.answer("Jinsingizni tanlang:", reply_markup=inline(
        [("👨 Erkak", "g:m"), ("👩 Ayol", "g:f")]))
    await state.set_state(Reg.gender)


@router.callback_query(Reg.gender, F.data.startswith("g:"))
async def reg_gender(c: CallbackQuery, state: FSMContext):
    await state.update_data(gender=c.data[2:])
    await c.message.edit_text("Jins tanlandi ✅")
    await c.message.answer("Yoshingiz (raqam bilan):")
    await state.set_state(Reg.age)


@router.message(Reg.age, F.text)
async def reg_age(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Yoshni raqam bilan yozing.")
    age = int(m.text)
    if age < 18:
        await state.clear()
        return await m.answer("Kechirasiz, bot faqat 18 yosh va undan kattalar uchun.")
    if age > 80:
        return await m.answer("Yoshni to'g'ri kiriting.")
    await state.update_data(age=age)
    await m.answer("Shahringiz:")
    await state.set_state(Reg.city)


@router.message(Reg.city, F.text)
async def reg_city(m: Message, state: FSMContext):
    await state.update_data(city=m.text.strip()[:40])
    await m.answer("Profil rasmingizni yuboring 📷:")
    await state.set_state(Reg.photo)


@router.message(Reg.photo, F.photo)
async def reg_photo(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    fid = m.photo[-1].file_id
    q("INSERT OR REPLACE INTO users(id,name,gender,age,city,photo,username,created) VALUES(?,?,?,?,?,?,?,?)",
      (m.from_user.id, d["name"], d["gender"], d["age"], d["city"], fid,
       m.from_user.username or "", int(time.time())), commit=True)
    await state.clear()
    g = "Erkak" if d["gender"] == "m" else "Ayol"
    await bot.send_photo(ADMIN_ID, fid, caption=(
        f"🆕 <b>Yangi foydalanuvchi</b>\n👤 {esc(d['name'])}, {g}, {d['age']}\n"
        f"🏙 {esc(d['city'])}\n🆔 <code>{m.from_user.id}</code>\n🔗 @{esc(m.from_user.username or '—')}"))
    await m.answer("✅ Ro'yxatdan o'tdingiz!")
    if await gate(m, bot):
        await m.answer("Asosiy menyu:", reply_markup=main_menu(m.from_user.id))


# ---------------------------------------------------------------- Profil / VIP
@router.message(F.text == "👤 Profilim")
async def profile(m: Message, bot: Bot):
    if not await gate(m, bot):
        return
    u = get_user(m.from_user.id)
    vip = "💎 VIP" if is_vip(u["id"]) else "🆓 Tekin"
    until = ""
    if is_vip(u["id"]):
        until = " (" + datetime.fromtimestamp(u["vip_until"]).strftime("%d.%m.%Y") + " gacha)"
    await m.answer_photo(u["photo"], caption=(
        f"👤 {esc(u['name'])}, {u['age']} yosh\n🏙 {esc(u['city'])}\nTarif: {vip}{until}"))


@router.message(F.text == "ℹ️ Yordam")
async def help_cmd(m: Message):
    await m.answer(intro_text())


@router.message(F.text == "💎 VIP obuna")
async def vip_info(m: Message, bot: Bot):
    if not get_user(m.from_user.id):
        return await m.answer("Avval /start bosing.")
    await m.answer(vip_text(), reply_markup=vip_kb())


@router.callback_query(F.data == "vip:receipt")
async def vip_receipt(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(Pay.receipt)
    await c.message.answer("To'lov chekining rasmini yuboring 📸", reply_markup=cancel_kb())


@router.message(Pay.receipt, F.photo)
async def got_receipt(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    u = get_user(m.from_user.id)
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=(
        f"🧾 <b>VIP to'lov</b>\n👤 {esc(u['name'])}\n🆔 <code>{u['id']}</code>"),
        reply_markup=inline([("✅ Tasdiqlash", f"pay:ok:{u['id']}"),
                             ("❌ Rad etish", f"pay:no:{u['id']}")]))
    await m.answer("⏳ Chek adminga yuborildi. Tekshirilgach xabar beramiz.",
                   reply_markup=main_menu(m.from_user.id))


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("pay:"))
async def admin_pay(c: CallbackQuery, bot: Bot):
    _, act, uid = c.data.split(":")
    uid = int(uid)
    if act == "ok":
        days = int(setting("vip_days"))
        u = get_user(uid)
        start_ts = max(time.time(), u["vip_until"])
        q("UPDATE users SET vip_until=? WHERE id=?", (int(start_ts + days * 86400), uid), commit=True)
        await bot.send_message(uid, f"✅ To'lov tasdiqlandi! 💎 VIP {days} kunga faollashdi.")
        await c.message.edit_caption(caption=c.message.caption + "\n\n✅ Tasdiqlandi")
    else:
        await bot.send_message(uid, "❌ To'lov tasdiqlanmadi. To'g'ri chek yuboring.")
        await c.message.edit_caption(caption=c.message.caption + "\n\n❌ Rad etildi")


# ---------------------------------------------------------------- Suhbatdosh topish
async def end_chat(uid, bot, notify=True):
    p = partner(uid)
    q("DELETE FROM pairs WHERE a=? OR b=?", (uid, uid), commit=True)
    if p and notify:
        try:
            await bot.send_message(p, "⛔ Suhbatdosh suhbatni tugatdi.", reply_markup=main_menu(p))
        except Exception:
            pass
    return p


async def notify_pair(bot, a, b):
    ua, ub = get_user(a), get_user(b)
    for to, other in ((a, ub), (b, ua)):
        g = "👨 Erkak" if other["gender"] == "m" else "👩 Ayol"
        note = "" if is_vip(to) else "\n\nℹ️ Tekin tarifda telefon, nik va havolalar yuborish taqiqlangan."
        try:
            await bot.send_message(to, f"✅ Suhbatdosh topildi!\n{g}, {other['age']} yosh\n\n"
                                       f"Yozishingiz mumkin 👇{note}", reply_markup=chat_kb())
        except Exception:
            pass


async def find_match(m, bot):
    uid = m.chat.id
    me = get_user(uid)
    want = "f" if me["gender"] == "m" else "m"
    row = q("SELECT user_id FROM queue WHERE gender=? AND user_id!=? ORDER BY ts LIMIT 1",
            (want, uid), one=True)
    if row:
        other = row["user_id"]
        q("DELETE FROM queue WHERE user_id IN (?,?)", (uid, other), commit=True)
        q("INSERT INTO pairs(a,b) VALUES(?,?)", (uid, other), commit=True)
        await notify_pair(bot, uid, other)
    else:
        q("INSERT OR REPLACE INTO queue VALUES(?,?,?)", (uid, me["gender"], int(time.time())), commit=True)
        await m.answer("🔎 Suhbatdosh qidirilmoqda... Topilganda xabar beraman.",
                       reply_markup=cancel_kb("❌ Qidiruvni bekor qilish"))


@router.message(F.text == "💬 Suhbatdosh topish")
async def search(m: Message, bot: Bot):
    if not await gate(m, bot):
        return
    uid = m.from_user.id
    if partner(uid):
        return await m.answer("Siz allaqachon suhbatdasiz.", reply_markup=chat_kb())
    await find_match(m, bot)


@router.message(F.text == "❌ Qidiruvni bekor qilish")
async def cancel_search(m: Message):
    q("DELETE FROM queue WHERE user_id=?", (m.from_user.id,), commit=True)
    await m.answer("Qidiruv bekor qilindi.", reply_markup=main_menu(m.from_user.id))


@router.message(F.text == "⛔ Tugatish")
async def stop_chat(m: Message, bot: Bot):
    if not partner(m.from_user.id):
        return await m.answer("Siz suhbatda emassiz.", reply_markup=main_menu(m.from_user.id))
    await end_chat(m.from_user.id, bot)
    await m.answer("Suhbat tugatildi.", reply_markup=main_menu(m.from_user.id))


@router.message(F.text == "⏭ Keyingisi")
async def next_chat(m: Message, bot: Bot):
    if partner(m.from_user.id):
        await end_chat(m.from_user.id, bot)
    if await gate(m, bot):
        await find_match(m, bot)


@router.message(F.text == "🔍 Kim bilan?")
async def who(m: Message, bot: Bot):
    p = partner(m.from_user.id)
    if not p:
        return await m.answer("Siz suhbatda emassiz.")
    if not is_vip(m.from_user.id):
        return await m.answer("🔒 Suhbatdosh kimligini va Telegram manzilini faqat VIP foydalanuvchilar ko'ra oladi.",
                              reply_markup=inline([("💎 VIP olish", "vip:info")], 1))
    u = get_user(p)
    link = f"@{u['username']}" if u["username"] else f'<a href="tg://user?id={p}">Profilga o\'tish</a>'
    await m.answer_photo(u["photo"], caption=(
        f"👤 {esc(u['name'])}, {u['age']} yosh\n🏙 {esc(u['city'])}\n🔗 {link}"))


bg_tasks = set()


async def delete_later(bot, chat_id, msg_id, secs):
    await asyncio.sleep(secs)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


@router.message(F.text == "🖼 Rasmini ko'rish")
async def view_photo(m: Message, bot: Bot):
    p = partner(m.from_user.id)
    if not p:
        return await m.answer("Siz suhbatda emassiz.")
    u = get_user(p)
    if is_vip(m.from_user.id):
        return await m.answer_photo(u["photo"], caption="🖼 Suhbatdosh rasmi", protect_content=True)
    secs = max(1, int(setting("photo_secs")))
    msg = await m.answer_photo(
        u["photo"], protect_content=True,
        caption=f"⏳ Bu rasm {secs} soniyadan keyin o'chadi.\n💎 VIP'da rasm doim ochiq turadi.")
    t = asyncio.create_task(delete_later(bot, m.chat.id, msg.message_id, secs))
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)


@router.callback_query(F.data == "vip:info")
async def vip_info_cb(c: CallbackQuery):
    await c.answer()
    await c.message.answer(vip_text(), reply_markup=vip_kb())


@router.message(F.text == "⚠️ Shikoyat")
async def report(m: Message, bot: Bot):
    p = partner(m.from_user.id)
    if not p:
        return await m.answer("Siz suhbatda emassiz.")
    q("INSERT OR IGNORE INTO watch VALUES(?)", (p,), commit=True)
    q("INSERT INTO reports V
