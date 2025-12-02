import os
import logging
import asyncio
import json
import psycopg2
from datetime import datetime
from pytz import timezone
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025') # Güncel model

# --- PYTHON ÖĞRENİYORUM SERİSİ KONULARI ---
PYTHON_TOPICS = [
    "Python näme? Giriş we gurnamak",
    "Ilkinji kodyň: Hello World we print()",
    "Üýtgeýänler (Variables) we maglumat görnüşleri (Data Types)",
    "Sanlar (Numbers) we matematiki amallar",
    "Setirler (Strings) we olar bilen işlemek",
    "Listler (Lists) - Giriş",
    "Dictionary (Sözlükler) we Tuples",
    "Şertli operatorlar: If, Elif, Else",
    "For Loop (Gaýtalanýan amallar)",
    "While Loop",
    "Funksiýalar (Functions) - Giriş",
    "Funksiýalarda parametrler we return",
    "Modullar we kitaphanalar (Modules)",
    "Hata dolandyryşy (Try, Except)",
    "Faýl amallary (Okamak we ýazmak)",
    "Klaslar we Obyektler (OOP Giriş)",
    # Buraya daha fazla konu ekleyebilirsin
]

# --- VERİTABANI İŞLEMLERİ ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Tabloları oluşturur"""
    conn = get_db_connection()
    cur = conn.cursor()
    # Ayarlar tablosu (Python serisi takibi için)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value INTEGER
        );
    """)
    # Varsayılan başlangıç değerini ata
    cur.execute("INSERT INTO settings (key, value) VALUES ('python_topic_index', 0) ON CONFLICT DO NOTHING;")
    
    # Bekleyen postlar tablosu (Draftlar)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_posts (
            type VARCHAR(20) PRIMARY KEY, -- 'morning', 'noon', 'evening', 'quiz'
            content TEXT,
            poll_data JSONB, -- Quiz için soru/cevap datası
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
    idx = cur.fetchone()[0]
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
    
    system_prompt = "Sen Türkmen dilinde ýazılım we tehnologiýa barada bilermen kömekçi. Ähli jogaplaryňy Türkmen dilinde (Latyn elipbiýinde) bermeli."
    
    prompts = {
        "morning": """
            Ertiriň haýyrly bolsun! Programmirleme, yazılım ýa-da tehnologiýa barada gysga, eglenceli, bilesigeliji (curiosity) fakt ýa-da peýdaly maslahat (tip) ýaz. 
            Tekst gysga we özüne çekiji bolsun. 
            Emojileri köp ulan. 
            Soňunda 2-3 sany degişli hashtag goş.
        """,
        "noon": f"""
            "Sıfırdan Python Öwrenýäris" seriýasy üçin post taýýarla.
            Bu günki mowzuk: "{topic}".
            
            Şu formatda bolmaly:
            1. Mowzugy düşnükli we sada dilde düşündir.
            2. Hökmany suratda kiçijik kod mysalyny (code snippet) goş.
            3. Emojiler bilen bezeg ber.
            4. Soňunda #python #tutorial #turkmenistan hashtaglerini ulan.
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
            response = model.generate_content(system_prompt + " " + user_prompt, generation_config={"response_mime_type": "application/json"})
            return json.loads(response.text)
        else:
            response = model.generate_content(system_prompt + " " + user_prompt)
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
        # Eğer konular bittiyse başa dön veya dur (burada başa dönüyoruz)
        safe_idx = idx % len(PYTHON_TOPICS)
        topic = PYTHON_TOPICS[safe_idx]

    # AI'dan içerik al
    logger.info(f"Generating content for {post_type}...")
    ai_result = await generate_content_ai(post_type, topic)
    
    content = ""
    poll_data = None

    if post_type == "quiz":
        content = ai_result['explanation'] # Quiz açıklamasını içerik olarak saklayalım veya boş bırakalım
        poll_data = ai_result
    else:
        content = ai_result
    
    # Veritabanına kaydet
    save_draft(post_type, content, poll_data)

    # Admine önizleme gönder
    keyboard = [[InlineKeyboardButton("♻️ Üýtget (Regenerate)", callback_data=f"regen_{post_type}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_prefix = f"📢 **YAYINA 1 SAAT VAR ({post_type.upper()})**\n\n"
    
    if post_type == "quiz":
        # Quiz önizlemesi
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
        # Normal post önizlemesi
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=msg_prefix + content, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

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
            poll_json = poll_data # Zaten jsonb olarak geliyor (psycopg2 dict döndürür)
            await context.bot.send_poll(
                chat_id=CHANNEL_ID,
                question=poll_json['question'],
                options=poll_json['options'],
                type=Poll.QUIZ,
                correct_option_id=poll_json['correct_option_id'],
                is_anonymous=True # Kanalda anonim olsun
            )
            # Quiz yayınlandıktan sonra konuyu ilerlet
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
    post_type = data.split("_")[1] # regen_morning -> morning

    topic = None
    if post_type in ['noon', 'quiz']:
        idx = get_topic_index()
        topic = PYTHON_TOPICS[idx % len(PYTHON_TOPICS)]

    # Yeni içerik üret
    ai_result = await generate_content_ai(post_type, topic)
    
    content = ""
    poll_data = None
    if post_type == "quiz":
        content = ai_result['explanation']
        poll_data = ai_result
    else:
        content = ai_result
    
    # DB Güncelle
    save_draft(post_type, content, poll_data)

    # Mesajı Güncelle (Admin panelinde)
    keyboard = [[InlineKeyboardButton("♻️ Üýtget (Regenerate)", callback_data=f"regen_{post_type}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if post_type == "quiz":
            # Poll'lar düzenlenemez, o yüzden eskiyi silip yeni atıyoruz
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
    
    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(regenerate_callback, pattern="^regen_"))

    # Scheduler (Zamanlayıcı)
    scheduler = AsyncIOScheduler(timezone=TZ)
    
    # --- ZAMANLAMA AYARLARI (SAATLER) ---
    # Sabah: 08:00 Hazırla -> 09:00 Paylaş
    scheduler.add_job(task_prepare_draft, 'cron', hour=8, minute=0, data={'type': 'morning'})
    scheduler.add_job(task_publish_post, 'cron', hour=9, minute=0, data={'type': 'morning'})

    # Öğle: 12:00 Hazırla -> 13:00 Paylaş (Python Serisi)
    scheduler.add_job(task_prepare_draft, 'cron', hour=12, minute=0, data={'type': 'noon'})
    scheduler.add_job(task_publish_post, 'cron', hour=13, minute=0, data={'type': 'noon'})

    # Akşam: 17:00 Hazırla -> 18:00 Paylaş (Alıştırma)
    scheduler.add_job(task_prepare_draft, 'cron', hour=17, minute=0, data={'type': 'evening'})
    scheduler.add_job(task_publish_post, 'cron', hour=18, minute=0, data={'type': 'evening'})

    # Test: 18:00 Hazırla -> 19:00 Paylaş (Konuyla ilgili Quiz - Posttan 1 saat sonra)
    scheduler.add_job(task_prepare_draft, 'cron', hour=18, minute=0, data={'type': 'quiz'})
    scheduler.add_job(task_publish_post, 'cron', hour=19, minute=0, data={'type': 'quiz'})

    scheduler.start()

    # Botu çalıştır
    print("Bot çalışıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
