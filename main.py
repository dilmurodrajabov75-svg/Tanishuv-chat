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
BOT_TOKEN = os.environ.get("8954402979:AAFO54qw9iphylm2b4odebRQhLSx1A7PBD4")
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


@router.message(F.text == "⚠️ Shikoyat")
async def report(m: Message, bot: Bot):
    p = partner(m.from_user.id)
    if not p:
        return await m.answer("Siz suhbatda emassiz.")
    q("INSERT OR IGNORE INTO watch VALUES(?)", (p,), commit=True)
    q("INSERT INTO reports VALUES(?,?,?)", (m.from_user.id, p, int(time.time())), commit=True)
    await bot.send_message(ADMIN_ID, f"⚠️ <b>Shikoyat</b>\nShikoyatchi: <code>{m.from_user.id}</code>\n"
                                     f"Shikoyat qilingan: <code>{p}</code> (kuzatuvga olindi)",
                           reply_markup=inline([("🚫 Bloklash", f"ban:{p}")], 1))
    await m.answer("Shikoyat adminga yuborildi. Rahmat.")


# ---------------------------------------------------------------- ADMIN PANEL
def admin_kb():
    return inline([
        ("📊 Statistika", "adm:stats"), ("🔎 Foydalanuvchini topish", "adm:find"),
        ("👥 Oxirgi foydalanuvchilar", "adm:users"), ("💎 VIP ro'yxati", "adm:vips"),
        ("💬 Faol suhbatlar", "adm:chats"), ("👁 Kuzatuv ro'yxati", "adm:watchlist"),
        ("➕ Kuzatuvga qo'shish", "adm:watch"), ("⚠️ Shikoyatlar", "adm:reports"),
        ("💎 VIP berish", "adm:grant"), ("✉️ Shaxsiy xabar", "adm:msg"),
        ("💳 Karta raqami", "set:card"), ("💵 VIP narxi", "set:price"),
        ("📅 VIP kunlari", "set:vip_days"), ("📢 Majburiy kanal", "set:channel"),
        ("⏱ Rasm soniyalari", "set:photo_secs"), ("📝 Kirish matni", "set:intro"),
        ("📨 Hammaga xabar", "adm:bc"), ("🚫 Bloklash", "adm:ban"),
        ("♻️ Blokdan chiqarish", "adm:unban"), ("🚫 Bloklanganlar", "adm:banned"),
    ])


@router.message(F.from_user.id == ADMIN_ID, F.text == "🛠 Admin panel")
async def admin_panel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=admin_kb())


SET_PROMPTS = {
    "card": "Yangi karta raqami (va egasi) ni yozing:",
    "price": "VIP narxini yozing (masalan: 50 000):",
    "vip_days": "VIP necha kunga berilsin (raqam):",
    "channel": "Majburiy kanal/guruh username'ini yozing (masalan: @kanal_nomi).\n"
               "Bot u yerda admin bo'lishi shart. O'chirish uchun «-» yozing:",
    "photo_secs": "Tekin foydalanuvchi suhbatdosh rasmini necha soniya ko'radi (raqam):",
    "intro": "Yangi foydalanuvchilarga ko'rsatiladigan tushuntirish matnini yozing.\n"
             "Standart matnga qaytish uchun «-» yozing:",
}
NUMERIC_KEYS = ("vip_days", "photo_secs")


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("set:"))
async def set_start(c: CallbackQuery, state: FSMContext):
    key = c.data[4:]
    await c.answer()
    await state.set_state(Adm.setting)
    await state.update_data(key=key)
    await c.message.answer(SET_PROMPTS[key])


@router.message(F.from_user.id == ADMIN_ID, Adm.setting, F.text)
async def set_save(m: Message, state: FSMContext):
    d = await state.get_data()
    val = m.text.strip()
    if d["key"] in NUMERIC_KEYS and not val.isdigit():
        return await m.answer("Faqat raqam yozing.")
    if d["key"] in ("channel", "intro") and val == "-":
        val = ""
    q("UPDATE settings SET v=? WHERE k=?", (val, d["key"]), commit=True)
    await state.clear()
    await m.answer("✅ Saqlandi.")


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("adm:"))
async def admin_cb(c: CallbackQuery, state: FSMContext):
    act = c.data[4:]
    await c.answer()
    if act == "stats":
        n = lambda sql: q(sql, one=True)[0]
        now = int(time.time())
        t = "📊 Foydalanuvchilar: " + str(n("SELECT COUNT(*) FROM users"))
        t += "\n👨 Erkak: " + str(n("SELECT COUNT(*) FROM users WHERE gender='m'"))
        t += "\n👩 Ayol: " + str(n("SELECT COUNT(*) FROM users WHERE gender='f'"))
        t += "\n💎 VIP: " + str(n("SELECT COUNT(*) FROM users WHERE vip_until>" + str(now)))
        t += "\n💬 Faol suhbatlar: " + str(n("SELECT COUNT(*) FROM pairs"))
        t += "\n🔎 Navbatda: " + str(n("SELECT COUNT(*) FROM queue"))
        t += "\n🆕 Bugun yangi: " + str(n("SELECT COUNT(*) FROM users WHERE created>" + str(now - 86400)))
        t += "\n🚫 Bloklangan: " + str(n("SELECT COUNT(*) FROM users WHERE banned=1"))
        t += "\n⚠️ Shikoyatlar: " + str(n("SELECT COUNT(*) FROM reports"))
        await c.message.answer(t)
    elif act == "users":
        rows = q("SELECT * FROM users ORDER BY rowid DESC LIMIT 20")
        t = "\n".join(f"<code>{r['id']}</code> {esc(r['name'])} {r['gender']} {r['age']}"
                      f"{' 💎' if r['vip_until'] > time.time() else ''}{' 🚫' if r['banned'] else ''}"
                      for r in rows) or "Yo'q"
        await c.message.answer("👥 Oxirgi 20 ta:\n\n" + t)
    elif act == "chats":
        rows = q("SELECT * FROM pairs LIMIT 20")
        if not rows:
            return await c.message.answer("Faol suhbatlar yo'q.")
        for r in rows:
            ua, ub = get_user(r["a"]), get_user(r["b"])
            await c.message.answer(
                f"💬 {esc(ua['name'])} ({r['a']}) ↔ {esc(ub['name'])} ({r['b']})",
                reply_markup=inline([("👁 Kuzatish", f"wp:{r['a']}:{r['b']}"),
                                     ("⛔ Uzish", f"kick:{r['a']}")]))
    elif act == "watchlist":
        rows = q("SELECT user_id FROM watch")
        t = "\n".join(f"<code>{r['user_id']}</code>" for r in rows) or "Bo'sh"
        await c.message.answer("👁 Kuzatuvdagilar:\n" + t,
                               reply_markup=inline([("🧹 Hammasini tozalash", "wclear")], 1))
    elif act == "find":
        await state.set_state(Adm.find)
        await c.message.answer("Foydalanuvchi ID yoki @username ni yozing:")
    elif act == "vips":
        rows = q("SELECT * FROM users WHERE vip_until>? ORDER BY vip_until", (int(time.time()),))
        t = "\n".join(f"<code>{r['id']}</code> {esc(r['name'])} — "
                      + datetime.fromtimestamp(r["vip_until"]).strftime("%d.%m.%Y") for r in rows)
        await c.message.answer("💎 Faol VIP'lar:\n\n" + (t or "Yo'q"))
    elif act == "banned":
        rows = q("SELECT * FROM users WHERE banned=1 LIMIT 30")
        if not rows:
            return await c.message.answer("Bloklanganlar yo'q.")
        for r in rows:
            await c.message.answer(f"🚫 {esc(r['name'])} (<code>{r['id']}</code>)",
                                   reply_markup=inline([("♻️ Blokdan chiqarish", f"unban:{r['id']}")], 1))
    elif act == "reports":
        rows = q("SELECT * FROM reports ORDER BY rowid DESC LIMIT 10")
        if not rows:
            return await c.message.answer("Shikoyatlar yo'q.")
        for r in rows:
            when = datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M")
            await c.message.answer(
                f"⚠️ {when}\nShikoyatchi: <code>{r['reporter']}</code>\nShikoyat qilingan: <code>{r['reported']}</code>",
                reply_markup=inline([("🔎 Profil", f"card:{r['reported']}"),
                                     ("🚫 Bloklash", f"ban:{r['reported']}")]))
    elif act == "msg":
        await state.set_state(Adm.msg)
        await c.message.answer("Format: <code>ID matn</code> (masalan: 123456789 Salom!)")
    elif act == "watch":
        await state.set_state(Adm.watch)
        await c.message.answer("Kuzatiladigan foydalanuvchi ID sini yozing:")
    elif act == "grant":
        await state.set_state(Adm.grant)
        await c.message.answer("Format: <code>ID KUN</code> (masalan: 123456789 30)")
    elif act == "bc":
        await state.set_state(Adm.broadcast)
        await c.message.answer("Hammaga yuboriladigan xabarni yozing:")
    elif act == "ban":
        await state.set_state(Adm.ban)
        await c.message.answer("Bloklanadigan ID ni yozing:")
    elif act == "unban":
        await state.set_state(Adm.unban)
        await c.message.answer("Blokdan chiqariladigan ID ni yozing:")


async def send_user_card(m, uid):
    u = get_user(uid)
    if not u:
        return await m.answer("Foydalanuvchi topilmadi.")
    g = "Erkak" if u["gender"] == "m" else "Ayol"
    vip = "yo'q"
    if u["vip_until"] > time.time():
        vip = datetime.fromtimestamp(u["vip_until"]).strftime("%d.%m.%Y") + " gacha"
    blocked = "ha" if u["banned"] else "yo'q"
    cap = (f"👤 {esc(u['name'])}, {g}, {u['age']}\n🏙 {esc(u['city'])}\n🆔 <code>{uid}</code>\n"
           f"🔗 @{esc(u['username'] or '—')}\n💎 VIP: {vip}\n🚫 Bloklangan: {blocked}")
    kb = inline([
        ("💎 VIP berish", f"gv:{uid}"), ("❌ VIP olish", f"rv:{uid}"),
        ("♻️ Blokdan chiqarish" if u["banned"] else "🚫 Bloklash",
         f"unban:{uid}" if u["banned"] else f"ban:{uid}"),
        ("👁 Kuzatish", f"wa:{uid}"), ("⛔ Suhbatni uzish", f"kick:{uid}"),
    ])
    await m.answer_photo(u["photo"], caption=cap, reply_markup=kb)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("card:"))
async def card_cb(c: CallbackQuery):
    await c.answer()
    await send_user_card(c.message, int(c.data[5:]))


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("gv:"))
async def give_vip_cb(c: CallbackQuery, bot: Bot):
    uid = int(c.data[3:])
    days = int(setting("vip_days"))
    u = get_user(uid)
    q("UPDATE users SET vip_until=? WHERE id=?",
      (int(max(time.time(), u["vip_until"]) + days * 86400), uid), commit=True)
    await c.answer(f"💎 {days} kun VIP berildi", show_alert=True)
    try:
        await bot.send_message(uid, f"💎 Sizga {days} kunlik VIP berildi!")
    except Exception:
        pass


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("rv:"))
async def revoke_vip_cb(c: CallbackQuery):
    q("UPDATE users SET vip_until=0 WHERE id=?", (int(c.data[3:]),), commit=True)
    await c.answer("VIP olib tashlandi", show_alert=True)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("unban:"))
async def unban_cb(c: CallbackQuery):
    q("UPDATE users SET banned=0 WHERE id=?", (int(c.data[6:]),), commit=True)
    await c.answer("Blokdan chiqarildi", show_alert=True)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("wa:"))
async def watch_add_cb(c: CallbackQuery):
    q("INSERT OR IGNORE INTO watch VALUES(?)", (int(c.data[3:]),), commit=True)
    await c.answer("Kuzatuvga qo'shildi 👁", show_alert=True)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("kick:"))
async def kick_cb(c: CallbackQuery, bot: Bot):
    uid = int(c.data[5:])
    p = await end_chat(uid, bot)
    try:
        await bot.send_message(uid, "⛔ Suhbat admin tomonidan tugatildi.", reply_markup=main_menu(uid))
    except Exception:
        pass
    await c.answer("Suhbat uzildi" if p else "Suhbat topilmadi", show_alert=True)


@router.message(F.from_user.id == ADMIN_ID, Adm.find, F.text)
async def adm_find(m: Message, state: FSMContext):
    await state.clear()
    t = m.text.strip()
    if t.isdigit():
        uid = int(t)
    else:
        r = q("SELECT id FROM users WHERE username=? COLLATE NOCASE", (t.lstrip("@"),), one=True)
        uid = r["id"] if r else 0
    await send_user_card(m, uid)


@router.message(F.from_user.id == ADMIN_ID, Adm.msg, F.text)
async def adm_msg(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    parts = m.text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        try:
            await bot.send_message(int(parts[0]), esc(parts[1]))
            await m.answer("✉️ Yuborildi.")
        except Exception:
            await m.answer("Yuborib bo'lmadi.")
    else:
        await m.answer("Format noto'g'ri.")


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("wp:"))
async def watch_pair(c: CallbackQuery):
    _, a, b = c.data.split(":")
    q("INSERT OR IGNORE INTO watch VALUES(?)", (int(a),), commit=True)
    q("INSERT OR IGNORE INTO watch VALUES(?)", (int(b),), commit=True)
    await c.answer("Kuzatuv boshlandi 👁", show_alert=True)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data == "wclear")
async def watch_clear(c: CallbackQuery):
    q("DELETE FROM watch", commit=True)
    await c.answer("Tozalandi", show_alert=True)


@router.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("ban:"))
async def ban_cb(c: CallbackQuery, bot: Bot):
    await do_ban(int(c.data[4:]), bot)
    await c.answer("Bloklandi", show_alert=True)


async def do_ban(uid, bot):
    q("UPDATE users SET banned=1 WHERE id=?", (uid,), commit=True)
    q("DELETE FROM queue WHERE user_id=?", (uid,), commit=True)
    await end_chat(uid, bot)


@router.message(F.from_user.id == ADMIN_ID, Adm.watch, F.text)
async def adm_watch(m: Message, state: FSMContext):
    await state.clear()
    if m.text.strip().isdigit():
        q("INSERT OR IGNORE INTO watch VALUES(?)", (int(m.text),), commit=True)
        await m.answer("👁 Kuzatuvga qo'shildi.")


@router.message(F.from_user.id == ADMIN_ID, Adm.grant, F.text)
async def adm_grant(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    parts = m.text.split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and get_user(int(parts[0])):
        uid, days = int(parts[0]), int(parts[1])
        u = get_user(uid)
        q("UPDATE users SET vip_until=? WHERE id=?",
          (int(max(time.time(), u["vip_until"]) + days * 86400), uid), commit=True)
        await m.answer("💎 VIP berildi.")
        await bot.send_message(uid, f"💎 Sizga {days} kunlik VIP berildi!")
    else:
        await m.answer("Format noto'g'ri yoki foydalanuvchi topilmadi.")


@router.message(F.from_user.id == ADMIN_ID, Adm.broadcast, F.text)
async def adm_bc(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    ok = 0
    for r in q("SELECT id FROM users WHERE banned=0"):
        try:
            await bot.send_message(r["id"], m.text)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await m.answer(f"📨 Yuborildi: {ok}")


@router.message(F.from_user.id == ADMIN_ID, Adm.ban, F.text)
async def adm_ban(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    if m.text.strip().isdigit():
        await do_ban(int(m.text), bot)
        await m.answer("🚫 Bloklandi.")


@router.message(F.from_user.id == ADMIN_ID, Adm.unban, F.text)
async def adm_unban(m: Message, state: FSMContext):
    await state.clear()
    if m.text.strip().isdigit():
        q("UPDATE users SET banned=0 WHERE id=?", (int(m.text),), commit=True)
        await m.answer("♻️ Blokdan chiqarildi.")


# ---------------------------------------------------------------- Suhbatni uzatish (eng oxirida)
@router.message(StateFilter(None))
async def relay(m: Message, bot: Bot):
    uid = m.from_user.id
    p = partner(uid)
    if not p:
        if q("SELECT 1 FROM queue WHERE user_id=?", (uid,), one=True):
            return await m.answer("🔎 Hali qidirilmoqda...")
        return await m.answer("Menyudan tanlang 👇", reply_markup=main_menu(uid))
    if not is_vip(uid):
        if m.content_type not in ("text", "sticker", "voice"):
            return await m.answer("🔒 Tekin tarifda faqat matn, stiker va ovozli xabar yuborish mumkin. "
                                  "Rasm/video uchun 💎 VIP kerak.")
        if m.text and has_contact(m.text):
            return await m.answer("🚫 Tekin tarifda telefon raqam, nik, havola va ilova nomlarini "
                                  "yuborish taqiqlangan. 💎 VIP oling.",
                                  reply_markup=inline([("💎 VIP olish", "vip:info")], 1))
    try:
        await bot.copy_message(p, m.chat.id, m.message_id)
    except Exception:
        await end_chat(uid, bot, notify=False)
        return await m.answer("Suhbatdosh botni tark etdi. Suhbat tugatildi.",
                              reply_markup=main_menu(uid))
    if q("SELECT 1 FROM watch WHERE user_id IN (?,?)", (uid, p), one=True):
        ua, ub = get_user(uid), get_user(p)
        try:
            await bot.send_message(ADMIN_ID, f"👁 {esc(ua['name'])} ({uid}) → {esc(ub['name'])} ({p})")
            await bot.copy_message(ADMIN_ID, m.chat.id, m.message_id)
        except Exception:
            pass


# ---------------------------------------------------------------- RUN
async def health(request):
    return web.Response(text="Bot ishlayapti")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "10000")))
    await site.start()


async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(BanMiddleware())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
                       
