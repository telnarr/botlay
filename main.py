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
import urllib.parse

# --- KONFİGÜRASYON ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
TZ = timezone('Asia/Ashgabat')

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GEMINI AI KURULUMU ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-exp')

# --- PYTHON ÖĞRENİYORUM SERİSİ KONULARI ---
PYTHON_TOPICS = [
    "Bölüm 1 - Python näme?",
    "Bölüm 2 - Näme üçin Python dilini saýlamaly?",
    "Bölüm 3 - Programmirleme dili näme zat?",
    "Bölüm 4 - Näme üçin programmirleme öwrenmeli?",
    "Bölüm 5 - Python ýüklemek",
    "Bölüm 6 - Pythona giriş",
    "Bölüm 7 - Python IDLE",
    "Bölüm 8 - CMD näme zat?",
    "Bölüm 9 - cmd-de iş köp ulanylan komandalar",
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
    "Bölüm 28 - input() funksiýasynda añsat mysallar",
    "Bölüm 29 - Şertli funksiýalary (if, elif, else)",
    "Bölüm 30 - If, elif, else barada",
    "Bölüm 31 - input, if we print ulanyp mysallar çözmek",
    "Bölüm 32 - wariabla baha bermek we şertli funksiýalarda ulanmak",
    "Bölüm 33 - Deñeşdirme funksiýalary",
    "Bölüm 34 - Gaýtalanma funksiýalary näme gerek? (for, while)",
    "Bölüm 35 - Gaýtalanmañ görnüşleri (for, while)",
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
            poll_data TEXT,
            image_url TEXT,
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

def save_draft(post_type, content, poll_data=None, image_url=None):
    conn = get_db_connection()
    cur = conn.cursor()
    poll_json = json.dumps(poll_data) if poll_data else None
    cur.execute("""
        INSERT INTO pending_posts (type, content, poll_data, image_url) 
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (type) 
        DO UPDATE SET content = EXCLUDED.content, poll_data = EXCLUDED.poll_data, image_url = EXCLUDED.image_url;
    """, (post_type, content, poll_json, image_url))
    conn.commit()
    cur.close()
    conn.close()

def get_draft(post_type):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT content, poll_data, image_url FROM pending_posts WHERE type = %s", (post_type,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    if res:
        content, poll_json, image_url = res
        poll_data = json.loads(poll_json) if poll_json else None
        return (content, poll_data, image_url)
    return None

def generate_image_url(keywords):
    """Pollinations AI ile görsel URL'i oluşturur"""
    prompt = urllib.parse.quote(keywords)
    return f"https://image.pollinations.ai/prompt/{prompt}"

# --- GEMINI İÇERİK ÜRETİMİ ---
async def generate_content_ai(post_type, topic=None):
    """Gemini API kullanarak içerik üretir"""
    
    system_prompt = """Sen Türkmen dilinde programmirleme we tehnologiýa barada bilermen kömeçi. 
    MÖHÜM: Jogaplaryñ diňe içerigiñ özi bolmaly. "Bolýar", "Tamam", "Ine" ýaly sözler bilen başlama. 
    Diňe post içerigini ber, başga hiç zat goşma."""
    
    prompts = {
        "morning": """
            Programmirleme, ýazılım ýa-da tehnologiýa barada gyzykly fakt ýa-da peýdaly maslahat ýaz.
            
            Format:
            - Gysgajyk başlyk (emoji bilen)
            - 2-3 sany sada we gysgajyk tekst abzasy
            - Emoji ulan
            - Soňunda 3 sany hashtag (#python #programming #tech)
            
            MÖHÜM: Diňe post içerigini ýaz. "Bolýar", "Ine", "Tamam" ýaly girişme sözler gerek däl.
        """,
        
        "noon": f"""
            "Başyndan Python Öwrenýäris" seriýasy üçin post taýýarla.
            Bu günki tema: "{topic}"
            
            Format:
            - Gyzykly başlyk (emoji bilen)
            - Temany sada we düşnükli düşündir (3-4 abzas)
            - Kiçijik kod mysaly goş (```python ... ```)
            - Emoji bilen bezeg ber
            - Soňunda #python #tutorial #turkmenistan hashtagler
            
            MÖHÜM: Diňe post içerigini ýaz. Başga söz goşma.
        """,
        
        "evening": """
            Programmirleme bilen baglanşykly kiçijik bir "Challenge" ýa-da "Alştyma" ýaz.
            
            Format:
            - Gyzykly başlyk (emoji bilen)
            - Mesele ýa-da alştyrmany düşündir (2-3 abzas)
            - Derejesini görkeziň (Añsat/Orta/Kyn)
            - Okyjylary teswirlerde jogap bermäge çagyr
            - Emoji ulan
            - Hashtag goş
            
            MÖHÜM: Diňe post içerigini ýaz. Başga söz goşma.
        """,
        
        "quiz": f"""
            "{topic}" mowzugy barada bir test soragyni taýýarla.
            
            Diňe JSON formatynda ber. Başga hiç hili söz ýazma.
            {{
                "question": "Soragyñ teksti (Türkmenče, gysga we açyk)",
                "options": ["Jogap A", "Jogap B", "Jogap C", "Jogap D"],
                "correct_option_id": 0,
                "explanation": "Näme üçin dogrudygyny gysgaça düşündir (1-2 sany)"
            }}
        """
    }

    try:
        user_prompt = prompts[post_type]
        
        if post_type == "quiz":
            response = await asyncio.to_thread(
                model.generate_content,
                system_prompt + "\n\n" + user_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        else:
            response = await asyncio.to_thread(
                model.generate_content,
                system_prompt + "\n\n" + user_prompt
            )
            return response.text
    except Exception as e:
        logger.error(f"AI Error ({post_type}): {e}")
        return None

async def generate_image_keywords(post_type, topic=None):
    """Görsel için anahtar kelime üretir"""
    prompts = {
        "morning": "technology programming code",
        "noon": f"python programming {topic.split('-')[1].strip() if topic else 'tutorial'}",
        "evening": "coding challenge programming",
        "quiz": "python quiz test question"
    }
    return prompts.get(post_type, "programming")

# --- BOT HANDLERS & TASKS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("✅ Salam Admin! Bot işjeň.\n\nKomandalar:\n/create - Post döret")

async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin'in istediği zamanda post oluşturmasını sağlar"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("🌅 Ertiriň Posta", callback_data="create_morning")],
        [InlineKeyboardButton("📚 Öýle Python Dersi", callback_data="create_noon")],
        [InlineKeyboardButton("💡 Agşam Challenge", callback_data="create_evening")],
        [InlineKeyboardButton("❓ Test Soragu", callback_data="create_quiz")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Haýsy post görnüşini döretmek isleýärsiňiz?", reply_markup=reply_markup)

async def create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create butonlarını işler"""
    query = update.callback_query
    await query.answer()
    
    post_type = query.data.replace("create_", "")
    await query.edit_message_text(f"⏳ {post_type.upper()} üçin içerik döredilýär...")
    
    # İçerik oluştur
    await prepare_draft_content(context, post_type, query.message.chat_id)

async def prepare_draft_content(context, post_type, chat_id):
    """İçerik hazırlama fonksiyonu"""
    topic = None
    if post_type in ['noon', 'quiz']:
        idx = get_topic_index()
        safe_idx = idx % len(PYTHON_TOPICS)
        topic = PYTHON_TOPICS[safe_idx]

    logger.info(f"Generating content for {post_type}...")
    ai_result = await generate_content_ai(post_type, topic)
    
    if not ai_result:
        await context.bot.send_message(chat_id=chat_id, text="❌ AI içerik üretemedi. Gaýtadan synanyşyň.")
        return
    
    content = ""
    poll_data = None
    image_url = None

    # Görsel oluştur
    keywords = await generate_image_keywords(post_type, topic)
    image_url = generate_image_url(keywords)

    if post_type == "quiz":
        content = ai_result.get('explanation', '')
        poll_data = ai_result
    else:
        content = ai_result
    
    save_draft(post_type, content, poll_data, image_url)

    keyboard = [
        [InlineKeyboardButton("✅ Kanala Ýaýynla", callback_data=f"publish_{post_type}")],
        [InlineKeyboardButton("♻️ Üýtget", callback_data=f"regen_{post_type}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_prefix = f"📢 **{post_type.upper()} TASLAMA**\n\n"
    
    try:
        if post_type == "quiz":
            q = poll_data
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"{msg_prefix}Soru: {q['question']}\n\nDoğru: {q['options'][q['correct_option_id']]}\n\nDüşündiriş: {q['explanation']}"
            )
            await context.bot.send_poll(
                chat_id=chat_id,
                question=q['question'],
                options=q['options'],
                type=Poll.QUIZ,
                correct_option_id=q['correct_option_id'],
                is_anonymous=False,
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=msg_prefix + content,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Send preview failed: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg_prefix + content,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# 1. Draft Oluşturma (Zamanlanmış)
async def task_prepare_draft(context: ContextTypes.DEFAULT_TYPE):
    post_type = context.job.data['type']
    await prepare_draft_content(context, post_type, ADMIN_ID)

# 2. Kanalda Yayınlama
async def task_publish_post(context: ContextTypes.DEFAULT_TYPE):
    post_type = context.job.data['type']
    
    draft = get_draft(post_type)
    if not draft:
        logger.error(f"No draft found for {post_type}")
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"⚠️ {post_type} üçin taslama tapylmady. /create ulanyp täzeden dörediň."
        )
        return

    content, poll_data, image_url = draft

    try:
        if post_type == "quiz":
            await context.bot.send_poll(
                chat_id=CHANNEL_ID,
                question=poll_data['question'],
                options=poll_data['options'],
                type=Poll.QUIZ,
                correct_option_id=poll_data['correct_option_id'],
                is_anonymous=True,
                explanation=poll_data.get('explanation', '')
            )
            increment_topic_index()
        else:
            if image_url:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=image_url,
                    caption=content
                )
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
            
            if post_type == "noon":
                increment_topic_index()
            
        logger.info(f"✅ Published {post_type}")
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ {post_type.upper()} kanala ýaýynlandy!")
        
    except Exception as e:
        logger.error(f"Publish failed: {e}")
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"⚠️ Hata: {post_type} ýaýynlanamady.\n{e}"
        )

# 3. Manuel Yayınlama Butonu
async def publish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Kanala ýaýynlanýar...")
    
    post_type = query.data.replace("publish_", "")
    
    draft = get_draft(post_type)
    if not draft:
        await query.edit_message_text("❌ Taslama tapylmady.")
        return

    content, poll_data, image_url = draft

    try:
        if post_type == "quiz":
            await context.bot.send_poll(
                chat_id=CHANNEL_ID,
                question=poll_data['question'],
                options=poll_data['options'],
                type=Poll.QUIZ,
                correct_option_id=poll_data['correct_option_id'],
                is_anonymous=True,
                explanation=poll_data.get('explanation', '')
            )
            increment_topic_index()
        else:
            if image_url:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=image_url,
                    caption=content
                )
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
            
            if post_type == "noon":
                increment_topic_index()
        
        await query.edit_message_text(f"✅ {post_type.upper()} kanala ýaýynlandy!")
        
    except Exception as e:
        logger.error(f"Manual publish failed: {e}")
        await query.edit_message_text(f"❌ Ýaýynlanyp bilmedi: {e}")

# 4. Yeniden Oluşturma
async def regenerate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Täzeden döredilýär...")
    
    post_type = query.data.replace("regen_", "")
    
    await query.edit_message_text(f"⏳ {post_type.upper()} täzeden döredilýär...")
    await prepare_draft_content(context, post_type, query.message.chat_id)

# --- MAIN SETUP ---

def main():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()
    job_queue = application.job_queue
    
    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("create", create_command))
    application.add_handler(CallbackQueryHandler(create_callback, pattern="^create_"))
    application.add_handler(CallbackQueryHandler(regenerate_callback, pattern="^regen_"))
    application.add_handler(CallbackQueryHandler(publish_callback, pattern="^publish_"))

    # --- ZAMANLAMA AYARLARI ---
    
    # Sabah: 08:00 Hazırla -> 09:00 Paylaş
    job_queue.run_daily(task_prepare_draft, time=time(8, 0, tzinfo=TZ), data={'type': 'morning'})
    job_queue.run_daily(task_publish_post, time=time(9, 0, tzinfo=TZ), data={'type': 'morning'})

    # Öğle: 12:00 Hazırla -> 13:00 Paylaş (Python Serisi)
    job_queue.run_daily(task_prepare_draft, time=time(12, 0, tzinfo=TZ), data={'type': 'noon'})
    job_queue.run_daily(task_publish_post, time=time(13, 0, tzinfo=TZ), data={'type': 'noon'})

    # Akşam: 17:00 Hazırla -> 18:00 Paylaş
    job_queue.run_daily(task_prepare_draft, time=time(17, 0, tzinfo=TZ), data={'type': 'evening'})
    job_queue.run_daily(task_publish_post, time=time(18, 0, tzinfo=TZ), data={'type': 'evening'})

    # Test: 20:00 Hazırla -> 21:00 Paylaş (Quiz)
    job_queue.run_daily(task_prepare_draft, time=time(20, 0, tzinfo=TZ), data={'type': 'quiz'})
    job_queue.run_daily(task_publish_post, time=time(21, 0, tzinfo=TZ), data={'type': 'quiz'})

    print("✅ Bot çalışıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
