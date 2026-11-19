
import os
import json
import time
import random
import asyncio
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

MIN_NUM = 1
MAX_NUM = 50
MAX_ATTEMPTS = 6
CHALLENGE_TIME = 120

DATA_FILE = os.environ.get("DATA_FILE", "scores.json")

games = {}
scores = {}
invitations = {}
challenges = {}
active_challenges = {}

POINTS = {
    1: 10,
    2: 8,
    3: 6,
    4: 4,
    5: 2,
    6: 1,
}

keyboard = ReplyKeyboardMarkup(
    [
        ["🎮 ابدأ التخمين"],
        ["🏆 قائمة المتصدرين", "🎁 المكافأة اليومية"],
        ["👥 تحدي صديق"],
    ],
    resize_keyboard=True
)


def load_scores():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        for entry in data.values():
            entry.setdefault("points", 0)
            entry.setdefault("wins", 0)
            entry.setdefault("losses", 0)
            entry.setdefault("last_daily", 0)
            entry.setdefault("name", "لاعب")
            entry.setdefault("username", "")

        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


scores = load_scores()


def save_scores():
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, DATA_FILE)


def register_user(user):
    uid = str(user.id)

    if uid not in scores:
        scores[uid] = {
            "name": user.first_name or "لاعب",
            "username": user.username or "",
            "points": 0,
            "wins": 0,
            "losses": 0,
            "last_daily": 0,
        }

    scores[uid]["name"] = user.first_name or "لاعب"
    scores[uid]["username"] = user.username or ""

    save_scores()
    return scores[uid]


def player_name(uid):
    entry = scores.get(str(uid), {})
    username = entry.get("username", "")

    if username:
        return "@" + username

    return entry.get("name", "لاعب")


def find_user_by_username(username):
    username = username.lstrip("@").lower()

    for uid, entry in scores.items():
        saved = entry.get("username", "").lower()

        if saved and saved == username:
            return uid

    return None


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

    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "أهلاً بك في لعبة تخمين الأرقام! 🎯\n\n"
        "اختر من القائمة بالأسفل:",
        reply_markup=keyboard
    )


async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    uid = str(user.id)

    if uid in active_challenges:
        await update.message.reply_text(
            "أنت داخل تحدي حالياً! أكمل التحدي أولاً."
        )
        return

    games[uid] = {
        "number": random.randint(MIN_NUM, MAX_NUM),
        "attempts": 0,
    }

    await update.message.reply_text(
        "🎮 بدأت لعبة جديدة!\n"
        "اخترت رقماً بين 1 و50.\n"
        "لديك 6 محاولات.\n\n"
        "اكتب الرقم مباشرة!",
        reply_markup=keyboard
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not scores:
        await update.message.reply_text("لا يوجد لاعبون حتى الآن!")
        return

    ranking = sorted(
        scores.items(),
        key=lambda item: item[1]["points"],
        reverse=True
    )[:10]

    medals = ["🥇", "🥈", "🥉"]
    message = "🏆 قائمة أفضل 10 لاعبين\n\n"

    for index, (uid, data) in enumerate(ranking):
        medal = medals[index] if index < 3 else f"{index + 1}."
        message += (
            f"{medal} {player_name(uid)}\n"
            f"💰 النقاط: {data['points']}\n"
            f"🏆 الانتصارات: {data['wins']}\n\n"
        )

    await update.message.reply_text(message)


async def daily_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    entry = register_user(user)

    now = time.time()
    remaining = 86400 - (now - entry["last_daily"])

    if remaining > 0:
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)

        await update.message.reply_text(
            f"🎁 حصلت على مكافأتك بالفعل!\n"
            f"ارجع بعد {hours} ساعة و{minutes} دقيقة."
        )
        return

    entry["points"] += 3
    entry["last_daily"] = now
    save_scores()

    await update.message.reply_text(
        "🎁 مبروك!\n"
        "حصلت على 3 نقاط يومية!\n"
        f"رصيدك الحالي: {entry['points']} نقطة."
    )


async def challenge_friend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if uid in active_challenges:
        await update.message.reply_text("أنت داخل تحدي حالياً!")
        return

    context.user_data["awaiting_opponent"] = True

    await update.message.reply_text(
        "👥 اكتب يوزر صديقك الآن.\n"
        "مثال: @ahmad\n\n"
        "يجب أن يكون صديقك قد بدأ البوت باستخدام /start."
    )


async def create_invitation(update: Update, context: ContextTypes.DEFAULT_TYPE, username):
    user = update.effective_user
    uid = str(user.id)

    opponent_id = find_user_by_username(username)

    if not opponent_id:
        await update.message.reply_text(
            "لم أجد هذا اللاعب.\n"
            "تأكد أنه ضغط /start في البوت وأن اليوزر صحيح."
        )
        return

    if opponent_id == uid:
        await update.message.reply_text("لا يمكنك تحدي نفسك 😄")
        return

    if uid in active_challenges or opponent_id in active_challenges:
        await update.message.reply_text(
            "أحد اللاعبين داخل تحدي حالياً."
        )
        return

    invitation_id = uuid.uuid4().hex[:12]

    invitations[invitation_id] = {
        "from": uid,
        "to": opponent_id,
    }

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ قبول التحدي",
                callback_data=f"accept:{invitation_id}"
            ),
            InlineKeyboardButton(
                "❌ رفض",
                callback_data=f"decline:{invitation_id}"
            ),
        ]
    ])

    await context.bot.send_message(
        chat_id=int(opponent_id),
        text=(
            f"⚔️ وصلك تحدي من {player_name(uid)}!\n\n"
            "هل تريد قبول التحدي؟"
        ),
        reply_markup=buttons
    )

    await update.message.reply_text(
        f"📨 تم إرسال دعوة التحدي إلى {player_name(opponent_id)}."
    )


async def handle_invitation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, invitation_id = query.data.split(":", 1)
    invitation = invitations.get(invitation_id)

    if not invitation:
        await query.edit_message_text("انتهت صلاحية هذه الدعوة.")
        return

    uid = str(query.from_user.id)

    if uid != invitation["to"]:
        await query.answer("هذه الدعوة ليست لك!", show_alert=True)
        return

    if action == "decline":
        del invitations[invitation_id]

        await query.edit_message_text("❌ رفضت التحدي.")

        await context.bot.send_message(
            chat_id=int(invitation["from"]),
            text=f"رفض {player_name(uid)} التحدي."
        )
        return

    first = invitation["from"]
    second = invitation["to"]

    if first in active_challenges or second in active_challenges:
        del invitations[invitation_id]
        await query.edit_message_text("أحد اللاعبين بدأ تحدياً آخر.")
        return

    del invitations[invitation_id]

    challenge_id = uuid.uuid4().hex[:12]
    starter = random.choice([first, second])

    challenges[challenge_id] = {
        "players": [first, second],
        "number": random.randint(MIN_NUM, MAX_NUM),
        "attempts": {
            first: 0,
            second: 0,
        },
        "turn": starter,
        "active": True,
    }

    active_challenges[first] = challenge_id
    active_challenges[second] = challenge_id

    await query.edit_message_text("✅ قبلت التحدي! بدأت المباراة.")

    await context.bot.send_message(
        chat_id=int(first),
        text=(
            f"⚔️ بدأ التحدي ضد {player_name(second)}!\n"
            "🎲 تم اختيار أول لاعب عشوائياً.\n"
            "⏱️ الوقت: دقيقتان.\n"
            "لكل لاعب 6 محاولات.\n\n"
            + ("🎯 دورك الآن! اكتب رقماً من 1 إلى 50."
               if starter == first else "⏳ انتظر دورك.")
        )
    )

    await context.bot.send_message(
        chat_id=int(second),
        text=(
            f"⚔️ بدأ التحدي ضد {player_name(first)}!\n"
            "🎲 تم اختيار أول لاعب عشوائياً.\n"
            "⏱️ الوقت: دقيقتان.\n"
            "لكل لاعب 6 محاولات.\n\n"
            + ("🎯 دورك الآن! اكتب رقماً من 1 إلى 50."
               if starter == second else "⏳ انتظر دورك.")
        )
    )

    asyncio.create_task(
        expire_challenge(challenge_id, context.bot)
    )


async def finish_challenge(challenge_id, bot, winner=None):
    challenge = challenges.get(challenge_id)

    if not challenge or not challenge["active"]:
        return

    challenge["active"] = False

    first, second = challenge["players"]

    for uid in [first, second]:
        active_challenges.pop(uid, None)

    secret = challenge["number"]

    if winner:
        attempts = challenge["attempts"][winner]
        points = POINTS[attempts] + 3

        scores[winner]["points"] += points
        scores[winner]["wins"] += 1

        loser = second if winner == first else first
        scores[loser]["losses"] += 1

        save_scores()

        result = (
            f"🏆 انتهى التحدي!\n"
            f"الفائز: {player_name(winner)}\n"
            f"الرقم الصحيح: {secret}\n"
            f"محاولاته: {attempts}\n"
            f"نقاط الفوز: {POINTS[attempts]}\n"
            f"مكافأة التحدي: 3 نقاط\n"
            f"مجموع النقاط المكتسبة: {points}"
        )
    else:
        result = (
            "⏱️ انتهى التحدي دون فائز!\n"
            f"الرقم الصحيح كان: {secret}"
        )

    for uid in [first, second]:
        try:
            await bot.send_message(
                chat_id=int(uid),
                text=result,
                reply_markup=keyboard
            )
        except Exception:
            pass


async def expire_challenge(challenge_id, bot):
    await asyncio.sleep(CHALLENGE_TIME)

    challenge = challenges.get(challenge_id)

    if challenge and challenge["active"]:
        await finish_challenge(challenge_id, bot)


async def process_guess(update, context, number):
    user = update.effective_user
    uid = str(user.id)

    register_user(user)

    if not MIN_NUM <= number <= MAX_NUM:
        await update.message.reply_text("أدخل رقماً بين 1 و50.")
        return

    challenge_id = active_challenges.get(uid)

    if challenge_id:
        challenge = challenges.get(challenge_id)

        if not challenge or not challenge["active"]:
            return

        if challenge["turn"] != uid:
            await update.message.reply_text("⏳ ليس دورك الآن، انتظر صديقك.")
            return

        challenge["attempts"][uid] += 1
        attempt = challenge["attempts"][uid]
        secret = challenge["number"]

        if number == secret:
            await finish_challenge(challenge_id, context.bot, uid)
            return

        remaining = MAX_ATTEMPTS - attempt
        hint = "أكبر 🔼" if number < secret else "أصغر 🔽"

        await update.message.reply_text(
            f"❌ الرقم السري {hint} من {number}.\n"
            f"محاولاتك المتبقية: {remaining}"
        )

        other = (
            challenge["players"][1]
            if uid == challenge["players"][0]
            else challenge["players"][0]
        )

        if challenge["attempts"][other] >= MAX_ATTEMPTS:
            if remaining <= 0:
                await finish_challenge(challenge_id, context.bot)
                return
            next_player = uid
        else:
            next_player = other

        challenge["turn"] = next_player

        if next_player == uid:
            await update.message.reply_text("🎯 دورك مرة ثانية!")
        else:
            await context.bot.send_message(
                chat_id=int(other),
                text=f"🎯 دورك الآن! {player_name(uid)} خمّن رقماً."
            )
        return

    current = games.get(uid)

    if not current:
        await update.message.reply_text(
            "ابدأ لعبة أولاً من زر 🎮 ابدأ التخمين."
        )
        return

    current["attempts"] += 1
    attempt = current["attempts"]

    if number == current["number"]:
        points = POINTS[attempt]

        scores[uid]["points"] += points
        scores[uid]["wins"] += 1
        save_scores()

        del games[uid]

        await update.message.reply_text(
            f"🏆 مبروك! الرقم الصحيح هو {number}!\n"
            f"عدد محاولاتك: {attempt}\n"
            f"💰 حصلت على {points} نقطة!\n"
            f"رصيدك: {scores[uid]['points']}",
            reply_markup=keyboard
        )
        return

    remaining = MAX_ATTEMPTS - attempt

    if remaining <= 0:
        scores[uid]["losses"] += 1
        save_scores()
        secret = current["number"]
        del games[uid]

        await update.message.reply_text(
            f"انتهت المحاولات!\n"
            f"الرقم كان {secret}.",
            reply_markup=keyboard
        )
        return

    hint = "أكبر 🔼" if number < current["number"] else "أصغر 🔽"

    await update.message.reply_text(
        f"الرقم السري {hint} من {number}.\n"
        f"المحاولات المتبقية: {remaining}"
    )


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب /guess ثم الرقم.")
        return

    try:
        number = int(context.args[0])
    except ValueError:
        await update.message.reply_text("أدخل رقماً صحيحاً.")
        return

    await process_guess(update, context, number)


async def stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if uid in active_challenges:
        challenge_id = active_challenges[uid]
        await finish_challenge(challenge_id, context.bot)
        return

    if uid in games:
        del games[uid]
        await update.message.reply_text(
            "تم إيقاف اللعبة.",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text("لا توجد لعبة نشطة.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = str(update.effective_user.id)

    register_user(update.effective_user)

    if context.user_data.get("awaiting_opponent"):
        context.user_data["awaiting_opponent"] = False
        await create_invitation(update, context, text)
        return

    if text == "🎮 ابدأ التخمين":
        await game(update, context)
    elif text == "🏆 قائمة المتصدرين":
        await leaderboard(update, context)
    elif text == "🎁 المكافأة اليومية":
        await daily_reward(update, context)
    elif text == "👥 تحدي صديق":
        await challenge_friend(update, context)
    elif text.isdigit():
        await process_guess(update, context, int(text))


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
    app.add_handler(CommandHandler("guess", guess_command))
    app.add_handler(CommandHandler("stopgame", stopgame))
    app.add_handler(CallbackQueryHandler(handle_invitation))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
