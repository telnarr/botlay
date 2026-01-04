import asyncio
import logging
import json
import random
import os
import asyncpg
from urllib.parse import urlparse
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery,
    WebAppInfo
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- YAPILANDIRMA (Environment Variables) ---
# Railway veya yerel ortamdan verileri çeker
API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Admin ID'lerini virgülle ayrılmış string olarak alıp listeye çeviriyoruz
# Örn env: ADMIN_IDS="123456789,987654321"
admin_env = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in admin_env.split(",")] if admin_env else []

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- GLOBAL DB HAVUZU ---
db_pool = None

# --- VERİTABANI İŞLEMLERİ (PostgreSQL/asyncpg) ---
async def init_db():
    global db_pool
    # Bağlantı havuzu oluştur
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    async with db_pool.acquire() as conn:
        # Kullanıcılar tablosu
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # İstatistik tablosu
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS quiz_stats (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                is_correct BOOLEAN
            )
        ''')

async def add_user(user_id, username):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username) 
            VALUES ($1, $2) 
            ON CONFLICT (user_id) DO NOTHING
        ''', user_id, username)

async def get_all_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
        return [row['user_id'] for row in rows]

async def save_quiz_result(user_id, is_correct):
    async with db_pool.acquire() as conn:
        await conn.execute('INSERT INTO quiz_stats (user_id, is_correct) VALUES ($1, $2)', user_id, is_correct)

async def get_stats():
    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
        total_attempts = await conn.fetchval('SELECT COUNT(*) FROM quiz_stats')
        correct_answers = await conn.fetchval('SELECT COUNT(*) FROM quiz_stats WHERE is_correct = TRUE')
        return total_users, total_attempts, correct_answers

# --- JSON SORU YÖNETİMİ ---
def load_questions():
    if not os.path.exists('questions.json'):
        return []
    with open('questions.json', 'r', encoding='utf-8') as f:
        return json.load(f)

# --- KLAVYE OLUŞTURUCU (Dinamik) ---
def get_main_keyboard(user_id):
    # Py mini butonu - Web App (Sohbet içinde açılır)
    # Kullanıcıya özel URL oluşturuluyor
    web_app_url = f"https://telnarr.pythonanywhere.com/{user_id}"
    
    buttons = [
        [
            KeyboardButton(text="Py mini", web_app=WebAppInfo(url=web_app_url)),
            KeyboardButton(text="Quiz")
        ]
    ]
    
    # Eğer kullanıcı Admin ise, Admin butonunu ekle
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="⚙️ Admin")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, persistent=True)

# Admin Menü Klavyesi
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Hemmä SMS")],
        [KeyboardButton(text="🔙 Asyl Menü")]
    ],
    resize_keyboard=True
)

# --- STATES ---
class AdminStates(StatesGroup):
    waiting_for_broadcast_message = State()

# --- HANDLERS ---
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    # DB'ye asenkron kayıt
    await add_user(user.id, user.username)
    
    # Kullanıcıya özel klavyeyi oluştur (Admin butonu kontrolü burada yapılıyor)
    kb = get_main_keyboard(user.id)
    
    await message.answer(
        f"Salam {user.first_name}! Hoş geldin.\n"
        "Aşakdaky knopgalary ulanyp bilersiň.",
        reply_markup=kb
    )

# NOT: "Py mini" butonu artık bir WebApp butonu olduğu için
# tıklandığında Telegram bir mesaj göndermez, doğrudan pencereyi açar.
# Bu yüzden Py mini için ayrı bir handler'a gerek yoktur.

@router.message(F.text == "Quiz")
async def process_quiz(message: types.Message):
    questions = load_questions()
    if not questions:
        await message.answer("Şu wagt sorag tapylanok.")
        return

    q_index = random.randint(0, len(questions) - 1)
    q_data = questions[q_index]
    options = q_data["cevaplar"]
    
    buttons = []
    for opt in options:
        buttons.append([
            InlineKeyboardButton(text=opt, callback_data=f"quiz:{q_index}:{opt}")
        ])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        f"❓ **Sorag:**\n{q_data['soru']}", 
        reply_markup=kb,
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("quiz:"))
async def check_quiz_answer(callback: CallbackQuery):
    try:
        _, q_index, user_answer = callback.data.split(":")
        q_index = int(q_index)
        
        questions = load_questions()
        if q_index >= len(questions):
            await callback.answer("Soragda näsazlyk çykdy.", show_alert=True)
            return

        correct_answer = questions[q_index]["dogru"]
        user_id = callback.from_user.id
        
        if user_answer == correct_answer:
            await save_quiz_result(user_id, True)
            await callback.answer("✅ Dogry jogap!", show_alert=True)
            await callback.message.edit_text(
                f"✅ **Dogry!**\n\nSorag: {questions[q_index]['soru']}\nJogabyň: {user_answer}",
                parse_mode="Markdown"
            )
        else:
            await save_quiz_result(user_id, False)
            await callback.answer(f"❌ Ýalňyş. Dogrysy: {correct_answer}", show_alert=True)
            await callback.message.edit_text(
                f"❌ **Ýalňyşş!**\n\nSorag: {questions[q_index]['soru']}\nDogry Jogap: {correct_answer}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Quiz ýalňyşlygy: {e}")
        await callback.answer("Ýalňyşlyk bar.")

# --- ADMIN PANELİ ---

# Hem /admin komutuyla hem de "Admin" butonuyla açılır
@router.message(Command("admin"))
@router.message(F.text == "⚙️ Admin")
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return # Admin değilse sessiz kal
    
    await message.answer("Admin panella.", reply_markup=admin_menu)

@router.message(F.text == "🔙 Asyl Menü")
async def back_to_main(message: types.Message):
    # Kullanıcı yetkisine göre klavyeyi tekrar hesapla
    kb = get_main_keyboard(message.from_user.id)
    await message.answer("Ana menüye dönüldü.", reply_markup=kb)

@router.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    users, attempts, correct = await get_stats()
    ratio = (correct / attempts * 100) if attempts > 0 else 0
    
    stats_msg = (
        "📊 **Bot Statistika (PostgreSQL)**\n\n"
        f"👥 Jemi Ulanyjy: `{users}`\n"
        f"📝 Çözülen Quiz: `{attempts}`\n"
        f"✅ Dogrylaň Sany: `{correct}`\n"
        f"📈 Üstünlik Prosent: `%{ratio:.2f}`"
    )
    await message.answer(stats_msg, parse_mode="Markdown")

@router.message(F.text == "📢 Hemmä SMS")
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer(
        "Hemme ulanyjylara ugradyljak sms y ýazyň (Surat/Faýl bolup biler).\n"
        "Otkaz üçin 'iptal' ýazyň.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_broadcast_message)

@router.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.text and message.text.lower() == 'iptal':
        await state.clear()
        kb = get_main_keyboard(message.from_user.id)
        await message.answer("İptal edildi.", reply_markup=admin_menu)
        return

    users = await get_all_users()
    count = 0
    blocked = 0
    
    status_msg = await message.answer(f"Ugradylyp başlanýar... ({len(users)} kişi)")
    
    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            count += 1
            await asyncio.sleep(0.05) 
        except Exception:
            blocked += 1
            
    await status_msg.edit_text(
        f"✅ Tamamlandy.\n\n"
        f"📨 Üstünlikli: {count}\n"
        f"🚫 Bolmady: {blocked}"
    )
    await message.answer("Admin paneli:", reply_markup=admin_menu)
    await state.clear()

# --- MAIN ---
async def main():
    if not API_TOKEN or not DATABASE_URL:
        print("HATA: BOT_TOKEN veya DATABASE_URL ayarlanmamış!")
        return

    # DB Bağlantısını Başlat
    await init_db()
    
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    print("Bot Railway/Postgres modunda çalışıyor...")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot durduruldu.")
