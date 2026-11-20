import os
import json
import time
import random
import hashlib
import secrets
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("guess-arena")

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Render health check: supports both GET and HEAD.
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"GUESS ARENA OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass

def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

def db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with db() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    account_name TEXT UNIQUE,
                    password_hash TEXT,
                    salt TEXT,
                    points INTEGER NOT NULL DEFAULT 0,
                    xp INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    last_daily BIGINT NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS friendships (
                    id BIGSERIAL PRIMARY KEY,
                    requester BIGINT NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    target BIGINT NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at BIGINT NOT NULL,
                    UNIQUE(requester, target)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS global_flags (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cur.execute("INSERT INTO global_flags(key,value) VALUES('ambassador_awarded','0') ON CONFLICT DO NOTHING")

def get_player(tg_id):
    with db() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM players WHERE telegram_id=%s", (tg_id,))
            return cur.fetchone()

def ensure_player(user):
    with db() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO players(telegram_id, username, created_at)
                VALUES(%s,%s,%s)
                ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username
            """, (user.id, user.username or user.first_name, int(time.time())))

def main_menu():
    return ReplyKeyboardMarkup([
        ["🎮 العب", "👤 الملف الشخصي"],
        ["🏆 المتصدرون", "🎁 تسجيل الدخول اليومي"],
        ["👥 الأصدقاء", "⚙️ إعداد الحساب"],
    ], resize_keyboard=True)

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()
    return salt, digest

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player(update.effective_user)
    await update.message.reply_text(
        "أهلاً بك في GUESS ARENA! 🏟️\nاختر من القائمة للبدء.",
        reply_markup=main_menu()
    )

async def account_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = get_player(update.effective_user.id)
    if p and p["account_name"]:
        context.user_data["account_step"] = "new_password"
        await update.message.reply_text("أرسل كلمة المرور الجديدة لتغييرها.")
    else:
        context.user_data["account_step"] = "account_name"
        await update.message.reply_text("أرسل اسم الحساب الذي تريد استخدامه.")

async def handle_account(update, context):
    step = context.user_data.get("account_step")
    if not step:
        return False
    value = update.message.text.strip()
    tg_id = update.effective_user.id
    if step == "account_name":
        if len(value) < 3 or len(value) > 24:
            await update.message.reply_text("اسم الحساب يجب أن يكون من 3 إلى 24 حرفاً.")
            return True
        with db() as con:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM players WHERE account_name=%s", (value,))
                if cur.fetchone():
                    await update.message.reply_text("هذا الاسم مستخدم. جرّب اسماً آخر.")
                    return True
        context.user_data["pending_account_name"] = value
        context.user_data["account_step"] = "set_password"
        await update.message.reply_text("ممتاز. أرسل كلمة مرور من 8 أحرف أو أكثر.")
        return True
    if step in ("set_password", "new_password"):
        if len(value) < 8:
            await update.message.reply_text("كلمة المرور يجب أن تكون 8 أحرف على الأقل.")
            return True
        salt, digest = hash_password(value)
        with db() as con:
            with con.cursor() as cur:
                if step == "set_password":
                    cur.execute("""
                        UPDATE players SET account_name=%s,password_hash=%s,salt=%s
                        WHERE telegram_id=%s
                    """, (context.user_data.pop("pending_account_name"), digest, salt, tg_id))
                else:
                    cur.execute("UPDATE players SET password_hash=%s,salt=%s WHERE telegram_id=%s",
                                (digest, salt, tg_id))
        context.user_data.pop("account_step", None)
        await update.message.reply_text("تم حفظ إعدادات الحساب بنجاح ✅", reply_markup=main_menu())
        return True
    return False

async def play(update, context):
    context.user_data["game"] = {
        "number": random.randint(1, 150),
        "attempt": 0,
        "max": 150,
        "scores": [10, 8, 6, 4, 2, 1],
        "xp": [100, 85, 70, 60, 50, 40],
    }
    await update.message.reply_text("🎮 اخترت الوضع العادي (1 إلى 150).\nعندك 6 محاولات. أرسل تخمينك كرقم.")

async def profile(update, context):
    p = get_player(update.effective_user.id)
    if not p:
        await update.message.reply_text("اكتب /start أولاً.")
        return
    level = max(1, p["xp"] // 500 + 1)
    await update.message.reply_text(
        f"👤 الملف الشخصي\n"
        f"الحساب: {p['account_name'] or 'غير مضبوط'}\n"
        f"النقاط: {p['points']}\nXP: {p['xp']}\n"
        f"المستوى: {level}\nالانتصارات: {p['wins']}"
    )

async def leaderboard(update, context):
    with db() as con:
        with con.cursor() as cur:
            cur.execute("SELECT account_name,username,points,wins FROM players ORDER BY points DESC,wins DESC LIMIT 10")
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("لا يوجد لاعبون في التصنيف بعد.")
        return
    lines = ["🏆 أفضل 10 لاعبين:"]
    for i, p in enumerate(rows, 1):
        lines.append(f"{i}. {p['account_name'] or p['username'] or 'لاعب'} — {p['points']} نقطة")
    await update.message.reply_text("\n".join(lines))

async def daily(update, context):
    tg_id = update.effective_user.id
    now = int(time.time())
    with db() as con:
        with con.cursor() as cur:
            cur.execute("SELECT last_daily FROM players WHERE telegram_id=%s", (tg_id,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("اكتب /start أولاً.")
                return
            if now - row["last_daily"] < 86400:
                remaining = 86400 - (now - row["last_daily"])
                hours, rem = divmod(remaining, 3600)
                await update.message.reply_text(f"استلمت مكافأتك اليومية. ارجع بعد {hours} ساعة تقريباً.")
                return
            cur.execute("UPDATE players SET xp=xp+20,last_daily=%s WHERE telegram_id=%s", (now, tg_id))
    await update.message.reply_text("🎁 حصلت على 20 XP لتسجيل الدخول اليومي!")

async def friends(update, context):
    await update.message.reply_text(
        "👥 نظام الأصدقاء قيد التجهيز في هذه النسخة.\n"
        "طلبات الصداقة والتحديات ستضاف في مرحلة لاحقة."
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if await handle_account(update, context):
        return
    text = update.message.text.strip()
    if text == "🎮 العب":
        await play(update, context); return
    if text == "👤 الملف الشخصي":
        await profile(update, context); return
    if text == "🏆 المتصدرون":
        await leaderboard(update, context); return
    if text == "🎁 تسجيل الدخول اليومي":
        await daily(update, context); return
    if text == "👥 الأصدقاء":
        await friends(update, context); return
    if text == "⚙️ إعداد الحساب":
        await account_setup(update, context); return

    game = context.user_data.get("game")
    if game:
        try:
            guess = int(text)
        except ValueError:
            await update.message.reply_text("أرسل رقماً صحيحاً بين 1 و150.")
            return
        if not 1 <= guess <= game["max"]:
            await update.message.reply_text(f"الرقم يجب أن يكون بين 1 و{game['max']}.")
            return
        game["attempt"] += 1
        attempt = game["attempt"]
        if guess == game["number"]:
            index = attempt - 1
            points = game["scores"][index]
            xp = game["xp"][index]
            with db() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE players SET points=points+%s,xp=xp+%s,wins=wins+1 WHERE telegram_id=%s",
                                (points, xp, update.effective_user.id))
            context.user_data.pop("game", None)
            await update.message.reply_text(f"🎉 صحيح! ربحت {points} نقطة و{xp} XP.")
        elif attempt >= 6:
            answer = game["number"]
            context.user_data.pop("game", None)
            await update.message.reply_text(f"انتهت المحاولات! الرقم كان {answer}.")
        else:
            hint = "أكبر 🔼" if guess < game["number"] else "أصغر 🔽"
            await update.message.reply_text(f"{hint}\nباقي لك {6-attempt} محاولات.")
        return
    await update.message.reply_text("اختر أمراً من القائمة أو اكتب /start.", reply_markup=main_menu())

def main():
    init_db()
    Thread(target=run_health_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    log.info("GUESS ARENA bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
