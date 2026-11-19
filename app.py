import os
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

MIN_NUM = 1
MAX_NUM = 50
MAX_ATTEMPTS = 7
games = {}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, *args):
        pass


def start_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في لعبة تخمين الأرقام! 🎯\n"
        "الأوامر:\n"
        "/game - بدء لعبة\n"
        "/guess 25 - تخمين رقم\n"
        "/stopgame - إيقاف اللعبة"
    )


async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    games[chat_id] = {
        "number": random.randint(MIN_NUM, MAX_NUM),
        "attempts": 0,
    }

    await update.message.reply_text(
        "🎮 بدأت لعبة جديدة!\n"
        "اخترت رقماً بين 1 و50.\n"
        "لديك 7 محاولات.\n"
        "اكتب /guess متبوعاً برقمك."
    )


async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = games.get(chat_id)

    if not current:
        await update.message.reply_text("ابدأ لعبة أولاً باستخدام /game")
        return

    if not context.args:
        await update.message.reply_text("اكتب مثالاً: /guess 25")
        return

    try:
        number = int(context.args[0])
    except ValueError:
        await update.message.reply_text("أدخل رقماً صحيحاً.")
        return

    if not MIN_NUM <= number <= MAX_NUM:
        await update.message.reply_text("الرقم لازم يكون بين 1 و50.")
        return

    current["attempts"] += 1

    if number == current["number"]:
        await update.message.reply_text(
            f"🏆 مبروك! الرقم الصحيح هو {number}!\n"
            f"عدد محاولاتك: {current['attempts']}"
        )
        del games[chat_id]
        return

    remaining = MAX_ATTEMPTS - current["attempts"]

    if remaining <= 0:
        await update.message.reply_text(
            f"انتهت المحاولات! الرقم كان {current['number']}."
        )
        del games[chat_id]
        return

    hint = "أكبر 🔼" if number < current["number"] else "أصغر 🔽"

    await update.message.reply_text(
        f"الرقم السري {hint} من {number}.\n"
        f"المحاولات المتبقية: {remaining}"
    )


async def stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in games:
        del games[chat_id]
        await update.message.reply_text("تم إيقاف اللعبة.")
    else:
        await update.message.reply_text("لا توجد لعبة نشطة.")


async def post_init(application):
    await application.bot.delete_webhook(drop_pending_updates=True)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")

    start_server()

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("game", game))
    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler("stopgame", stopgame))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
