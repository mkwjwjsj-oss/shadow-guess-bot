
import os
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

MIN_NUM = 1
MAX_NUM = 50
MAX_ATTEMPTS = 7

games = {}
SCORES_FILE = "scores.json"

keyboard = ReplyKeyboardMarkup(
    [
        ["🎮 ابدأ التخمين"],
        ["🏆 قائمة المتصدرين"],
    ],
    resize_keyboard=True
)


def load_scores():
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


scores = load_scores()


def save_scores():
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


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
        "أهلاً بك في لعبة تخمين الأرقام! 🎯\n\n"
        "اختر من الأزرار بالأسفل:",
        reply_markup=keyboard
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
        "لديك 7 محاولات.\n\n"
        "اكتب الرقم مباشرة!",
        reply_markup=keyboard
    )


async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not scores:
        await update.message.reply_text(
            "🏆 قائمة المتصدرين\n\n"
            "لا يوجد لاعبون حتى الآن!"
        )
        return

    ranking = sorted(
        scores.items(),
        key=lambda item: item[1]["wins"],
        reverse=True
    )[:10]

    medals = ["🥇", "🥈", "🥉"]

    message = "🏆 أفضل 10 لاعبين\n\n"

    for index, (user_id, data) in enumerate(ranking):
        medal = medals[index] if index < 3 else f"{index + 1}."
        message += (
            f"{medal} {data['name']}\n"
            f"   🏆 الانتصارات: {data['wins']}\n\n"
        )

    await update.message.reply_text(message)


async def process_guess(update, context, number):
    chat_id = update.effective_chat.id
    user = update.effective_user
    current = games.get(chat_id)

    if not current:
        await update.message.reply_text(
            "ابدأ لعبة أولاً بالضغط على 🎮 ابدأ التخمين",
            reply_markup=keyboard
        )
        return

    if not MIN_NUM <= number <= MAX_NUM:
        await update.message.reply_text("الرقم لازم يكون بين 1 و50.")
        return

    current["attempts"] += 1

    if number == current["number"]:
        user_id = str(user.id)
        name = user.first_name or user.username or "لاعب"

        if user.username:
            name = "@" + user.username

        if user_id not in scores:
            scores[user_id] = {"name": name, "wins": 0}

        scores[user_id]["name"] = name
        scores[user_id]["wins"] += 1
        save_scores()

        await update.message.reply_text(
            f"🏆 مبروك يا {name}!\n"
            f"الرقم الصحيح هو {number}!\n"
            f"عدد المحاولات: {current['attempts']}\n\n"
            "تم تسجيل فوزك في قائمة المتصدرين!",
            reply_markup=keyboard
        )

        del games[chat_id]
        return

    remaining = MAX_ATTEMPTS - current["attempts"]

    if remaining <= 0:
        await update.message.reply_text(
            f"انتهت المحاولات! 😢\n"
            f"الرقم كان {current['number']}.\n"
            "اضغط ابدأ التخمين للعب من جديد.",
            reply_markup=keyboard
        )
        del games[chat_id]
        return

    hint = "أكبر 🔼" if number < current["number"] else "أصغر 🔽"

    await update.message.reply_text(
        f"❌ الرقم السري {hint} من {number}.\n"
        f"المحاولات المتبقية: {remaining}"
    )


async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب مثالاً: /guess 25")
        return

    try:
        number = int(context.args[0])
    except ValueError:
        await update.message.reply_text("أدخل رقماً صحيحاً.")
        return

    await process_guess(update, context, number)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "🎮 ابدأ التخمين":
        await game(update, context)
        return

    if text == "🏆 قائمة المتصدرين":
        await show_leaderboard(update, context)
        return

    if text.isdigit():
        await process_guess(update, context, int(text))


async def stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in games:
        del games[chat_id]
        await update.message.reply_text(
            "تم إيقاف اللعبة.",
            reply_markup=keyboard
        )
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
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
