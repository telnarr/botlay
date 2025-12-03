import os
import logging
import asyncio
import json
import psycopg2
from datetime import datetime, time
from pytz import timezone
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- KONFİGÜRASYON ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
TZ = timezone('Asia/Ashgabat')  # Türkmenistan Saati

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GEMINI AI KURULUMU ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')

# --- PYTHON ÖĞRENİYORUM SERİSİ KONULARI ---
PYTHON_TOPICS = [
    "Bölüm 1 - Python näme?",
    "Bölüm 2 - Näme üçin Python dilini saýlamaly? ",
    "Bölüm 3 - Programmirleme dili näme zat?",
    "Bölüm 4 - Näme üçin programmirleme öwrenmeli?",
    "Bölüm 5 - Python ýüklemek",
    "Bölüm 6 - Pythona giriş",
    "Bölüm 7 - Python IDLE",
    "Bölüm 8 - CMD näme zat?"
    "Bölüm 9 - cmd-de iň köp ulanylýan komandalar",
    "Bölüm 10 - cmd-de dir komandasy",
    "Bölüm 11 - cmd-de cd komandasy",
    "Bölüm 12 - cmd-de md komandasy",
    "Bölüm 13 - cmd-de rd komandasy",
    "Bölüm 14 - cmd-de del komandasy",
    "Bölüm 15 - Python kody işletmek",
    "Bölüm 16 - Pythonda esasy type lar",
    "Bölüm 17 - Integer",
    "Bölüm 18 - String",
    "Bölüm 19 - Float",
    "Bölüm 20 - Ilkinji programma",
    "Bölüm 21 - Print kody",
    "Bölüm 22 - Goşmak operatory +",
    "Bölüm 23 - Aýyrmak operatory -",
    "Bölüm 24 - Köpeltmek operatory *",
    "Bölüm 25 - Bölmek operatory /",
    "Bölüm 26 - Div we Mod",
    "Bölüm 27 - input() funksiýasy",
    "Bölüm 28 - input() funksiýasynda aňsat mysallar",
    "Bölüm 29 - Şertli funksiýalary (if, elif, else)",
    "Bölüm 30 - If, elif, else barada",
    "Bölüm 31 - input, if we print ulanyp mysallar çözmek",
    "Bölüm 32 - wariabla baha bermek we şertli funksiýalarda ulanmak",
    "Bölüm 33 - Deňeşdirme funksiýalary",
    "Bölüm 34 - Gaýtalanma funksiýalary nämä gerek ? (for, while)",
    "Bölüm 35 - Gaýtalanmaň görnüşleri (for, while)",
    
]

# --- VERİTABANI İŞLEMLERİ ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Tabloları oluşturur"""
    conn = get_db_connection()
    cur = conn.cursor()
    # Ayarlar tablosu
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value INTEGER
        );
    """)
    cur.execute("INSERT INTO settings (key, value) VALUES ('python_topic_index', 0) ON CONFLICT DO NOTHING;")
    
    # Bekleyen postlar tablosu
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_posts (
            type VARCHAR(20) PRIMARY KEY,
            content TEXT,
            poll_data JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_topic_index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'python_topic_index'")
    row = cur.fetchone()
    idx = row[0] if row else 0
    cur.close()
    conn.close()
    return idx

def increment_topic_index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET value = value + 1 WHERE key = 'python_topic_index'")
    conn.commit()
    cur.close()
    conn.close()

def save_draft(post_type, content, poll_data=None):
    conn = get_db_connection()
    cur = conn.cursor()
    poll_json = json.dumps(poll_data) if poll_data else None
    cur.execute("""
        INSERT INTO pending_posts (type, content, poll_data) 
        VALUES (%s, %s, %s)
        ON CONFLICT (type) 
        DO UPDATE SET content = EXCLUDED.content, poll_data = EXCLUDED.poll_data;
    """, (post_type, content, poll_json))
    conn.commit()
    cur.close()
    conn.close()

def get_draft(post_type):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT content, poll_data FROM pending_posts WHERE type = %s", (post_type,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res

# --- GEMINI İÇERİK ÜRETİMİ ---
async def generate_content_ai(post_type, topic=None):
    """Gemini API kullanarak içerik üretir"""
    
    system_prompt = "Sen Türkmen dilinde programmirleme we tehnologiýa barada bilermen kömekçi. Ähli jogaplaryňy Türkmen dilinde (Latyn elipbiýinde) bermeli."
    
    prompts = {
        "morning": """
            Ertiriň haýyrly bolsun! Programmirleme, yazılım ýa-da tehnologiýa barada gysga, gyzykly, bilesigeliji (curiosity) fakt ýa-da peýdaly maslahat (tip) ýaz. 
            Tekst gysga we özüne çekiji bolsun. 
            Emojileri köp ulan. 
            Soňunda 2-3 sany degişli hashtag goş.
        """,
        "noon": f"""
            "Başyndan Python Öwrenýäris" seriýasy üçin gaty uzyn bolmadyk post taýýarla.
            Bu günki tema: "{topic}".
            
            Şu formatda bolmaly:
            1. Temany düşnükli we sada dilde düşündir.
            2. Hökmany suratda kiçijik kod mysalyny (code snippet) goş.
            3. Emojiler bilen bezeg ber.
            4. Soňunda #python #tutorial #turkmenistan ýaly hashtagler ulan.
        """,
        "evening": """
            Agşamyňyz haýyrly bolsun! Programmirleme bilen baglanyşykly kiçijik bir "Challenge" ýa-da "Alıştırma" (Practice) ýaz.
            Derejesi tötänleýin bolsun (Aňsat, Orta ýa-da Kyn).
            Okyjylary teswirlerde (kommentariýalarda) jogap bermäge çagyr.
            Emojiler ulan. Hashtag goş.
        """,
        "quiz": f"""
            Bu günki öwrenilen Python mowzugy "{topic}" barada bir sany test soragyny taýýarla.
            
            Muny diňe JSON formatynda bermeli. Başga hiç hili söz ýazma.
            Format şeýle bolsun:
            {{
                "question": "Soragyň teksti (Türkmençe)",
                "options": ["Jogap A", "Jogap B", "Jogap C", "Jogap D"],
                "correct_option_id": 0,
                "explanation": "Näme üçin dogrydygyny gysgaça düşündir."
            }}
            (correct_option_id: 0 bolsa birinji jogap dogry, 1 bolsa ikinji, we ş.m.)
        """
    }

    try:
        user_prompt = prompts[post_type]
        if post_type == "quiz":
            response = await asyncio.to_thread(
                model.generate_content,
                system_prompt + " " + user_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        else:
            response = await asyncio.to_thread(
                model.generate_content,
                system_prompt + " " + user_prompt
            )
            return response.text
    except Exception as e:
        logger.error(f"AI Error ({post_type}): {e}")
        return "Bagyşlaň, AI bir säwlik goýberdi. Gaýtadan synanşyň."

# --- BOT HANDLERS & TASKS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Salam Admin! Bot işjeň. Gündelik tertip boýunça işlemäge taýýar.")

# 1. Draft Oluşturma ve Admine Gönderme Fonksiyonu
async def task_prepare_draft(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    post_type = job_data['type']
    
    topic = None
    if post_type in ['noon', 'quiz']:
        idx = get_topic_index()
        safe_idx = idx % len(PYTHON_TOPICS)
        topic = PYTHON_TOPICS[safe_idx]

    logger.info(f"Generating content for {post_type}...")
    ai_result = await generate_content_ai(post_type, topic)
    
    content = ""
    poll_data = None

    if post_type == "quiz":
        content = ai_result.get('explanation', '')
        poll_data = ai_result
    else:
        content = ai_result
    
    save_draft(post_type, content, poll_data)

    keyboard = [[InlineKeyboardButton("♻️ Üýtget (Regenerate)", callback_data=f"regen_{post_type}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_prefix = f"📢 **YAYINA 1 SAAT VAR ({post_type.upper()})**\n\n"
    
    try:
        if post_type == "quiz":
            q = poll_data
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"{msg_prefix}Soru: {q['question']}\nCevaplar: {q['options']}\nDoğru: {q['options'][q['correct_option_id']]}")
            await context.bot.send_poll(
                chat_id=ADMIN_ID,
                question=q['question'],
                options=q['options'],
                type=Poll.QUIZ,
                correct_option_id=q['correct_option_id'],
                is_anonymous=False,
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID, 
                text=msg_prefix + content, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Send admin preview failed: {e}")

# 2. Kanalda Yayınlama Fonksiyonu
async def task_publish_post(context: ContextTypes.DEFAULT_TYPE):
    post_type = context.job.data['type']
    
    draft = get_draft(post_type)
    if not draft:
        logger.error(f"No draft found for {post_type}")
        return

    content, poll_data = draft

    try:
        if post_type == "quiz":
            poll_json = poll_data 
            await context.bot.send_poll(
                chat_id=CHANNEL_ID,
                question=poll_json['question'],
                options=poll_json['options'],
                type=Poll.QUIZ,
                correct_option_id=poll_json['correct_option_id'],
                is_anonymous=True 
            )
            increment_topic_index()
        else:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
            
        logger.info(f"Published {post_type}")
    except Exception as e:
        logger.error(f"Publish failed: {e}")
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Hata: {post_type} yayınlanamadı.\n{e}")

# 3. Yeniden Oluşturma (Regenerate) Butonu
async def regenerate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Täzeden döredilýär...")
    
    data = query.data
    post_type = data.split("_")[1]

    topic = None
    if post_type in ['noon', 'quiz']:
        idx = get_topic_index()
        topic = PYTHON_TOPICS[idx % len(PYTHON_TOPICS)]

    ai_result = await generate_content_ai(post_type, topic)
    
    content = ""
    poll_data = None
    if post_type == "quiz":
        content = ai_result.get('explanation', '')
        poll_data = ai_result
    else:
        content = ai_result
    
    save_draft(post_type, content, poll_data)

    keyboard = [[InlineKeyboardButton("♻️ Üýtget (Regenerate)", callback_data=f"regen_{post_type}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if post_type == "quiz":
            await query.message.delete()
            q = poll_data
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"📢 **YENİLENDİ ({post_type.upper()})**\nSoru: {q['question']}\nDoğru: {q['options'][q['correct_option_id']]}")
            await context.bot.send_poll(
                chat_id=ADMIN_ID,
                question=q['question'],
                options=q['options'],
                type=Poll.QUIZ,
                correct_option_id=q['correct_option_id'],
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                text=f"📢 **YENİLENDİ ({post_type.upper()})**\n\n{content}",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Edit message failed: {e}")

# --- MAIN SETUP ---

def main():
    # Veritabanını başlat
    init_db()

    # Uygulamayı oluştur
    application = Application.builder().token(BOT_TOKEN).build()
    job_queue = application.job_queue
    
    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(regenerate_callback, pattern="^regen_"))

    # --- ZAMANLAMA AYARLARI (PTB JobQueue Kullanılarak) ---
    
    # Sabah: 08:00 Hazırla -> 09:00 Paylaş
    job_queue.run_daily(task_prepare_draft, time=time(15, 28, tzinfo=TZ), data={'type': 'morning'})
    job_queue.run_daily(task_publish_post, time=time(15, 30, tzinfo=TZ), data={'type': 'morning'})

    # Öğle: 12:00 Hazırla -> 13:00 Paylaş (Python Serisi)
    job_queue.run_daily(task_prepare_draft, time=time(15, 28, tzinfo=TZ), data={'type': 'noon'})
    job_queue.run_daily(task_publish_post, time=time(15, 30, tzinfo=TZ), data={'type': 'noon'})

    # Akşam: 17:00 Hazırla -> 18:00 Paylaş (Alıştırma)
    job_queue.run_daily(task_prepare_draft, time=time(15, 28, tzinfo=TZ), data={'type': 'evening'})
    job_queue.run_daily(task_publish_post, time=time(15, 30, tzinfo=TZ), data={'type': 'evening'})

    # Test: 18:00 Hazırla -> 19:00 Paylaş (Quiz)
    job_queue.run_daily(task_prepare_draft, time=time(15, 28, tzinfo=TZ), data={'type': 'quiz'})
    job_queue.run_daily(task_publish_post, time=time(15, 30, tzinfo=TZ), data={'type': 'quiz'})

    # Botu çalıştır
    print("Bot çalışıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
