# GUESS ARENA - corrected full app.py
# GUESS ARENA build: friends challenge integrated into original Stage 2
import os
import time
import random
import logging
import secrets

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("guess-arena")

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


def start_health_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"GUESS ARENA OK")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()


def connect():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=15,
    )


def init_db():
    with connect() as con:
        with con.cursor() as cur:
            # Create the current players table for a fresh database.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    points INTEGER NOT NULL DEFAULT 0,
                    xp INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    last_daily BIGINT NOT NULL DEFAULT 0,
                    is_banned BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at BIGINT NOT NULL DEFAULT 0
                )
            """)

            # Migrate older Stage 1 databases without deleting player data.
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS username TEXT")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS display_name TEXT")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS xp INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS wins INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS losses INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_daily BIGINT NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT 0")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    invite_code TEXT NOT NULL UNIQUE,
                    leader_id BIGINT NOT NULL REFERENCES players(telegram_id),
                    team_points INTEGER NOT NULL DEFAULT 0,
                    season_points INTEGER NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
                    player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at BIGINT NOT NULL,
                    PRIMARY KEY (team_id, player_id),
                    UNIQUE (player_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id BIGSERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'solo',
                    status TEXT NOT NULL DEFAULT 'open',
                    starts_at BIGINT,
                    prize_points INTEGER NOT NULL DEFAULT 0,
                    max_players INTEGER NOT NULL DEFAULT 32,
                    created_by BIGINT NOT NULL,
                    created_at BIGINT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_entries (
                    tournament_id BIGINT REFERENCES tournaments(id) ON DELETE CASCADE,
                    player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                    team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
                    joined_at BIGINT NOT NULL,
                    PRIMARY KEY (tournament_id, player_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS shop_items (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    duration_hours INTEGER,
                    uses INTEGER,
                    active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id BIGSERIAL PRIMARY KEY,
                    player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                    item_id BIGINT REFERENCES shop_items(id),
                    remaining_uses INTEGER,
                    expires_at BIGINT,
                    purchased_at BIGINT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id BIGSERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    starts_at BIGINT NOT NULL,
                    ends_at BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS season_deductions (
                    season_id BIGINT REFERENCES seasons(id) ON DELETE CASCADE,
                    player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                    deducted_points INTEGER NOT NULL,
                    PRIMARY KEY (season_id, player_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS friend_challenges (
                    id BIGSERIAL PRIMARY KEY,
                    challenger_id BIGINT NOT NULL REFERENCES players(telegram_id),
                    opponent_id BIGINT NOT NULL REFERENCES players(telegram_id),
                    secret_number INTEGER NOT NULL CHECK (secret_number BETWEEN 1 AND 150),
                    status TEXT NOT NULL DEFAULT 'pending',
                    current_turn BIGINT,
                    challenger_attempts INTEGER NOT NULL DEFAULT 0,
                    opponent_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL,
                    finished_at BIGINT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_friend_challenges_players_status
                ON friend_challenges(challenger_id, opponent_id, status)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS match_history (
                    id BIGSERIAL PRIMARY KEY,
                    player_id BIGINT NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
                    result TEXT NOT NULL CHECK (result IN ('win', 'loss')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    points_earned INTEGER NOT NULL DEFAULT 0,
                    xp_earned INTEGER NOT NULL DEFAULT 0,
                    played_at BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_match_history_player_time
                ON match_history(player_id, played_at DESC)
            """)

            cur.execute("""
                INSERT INTO shop_items(code, title, price, duration_hours, uses)
                VALUES
                    ('POINTS_X2', 'مضاعفة النقاط', 75, 24, NULL),
                    ('XP_X2', 'مضاعفة XP', 75, 24, NULL),
                    ('POINTS_SHIELD', 'حماية النقاط', 75, 24, NULL),
                    ('ONE_USE_150', 'عنصر استخدام واحد', 150, NULL, 1),
                    ('ONE_USE_300', 'عنصر استخدام واحد مميز', 300, NULL, 1)
                ON CONFLICT (code) DO NOTHING
            """)


def ensure_player(user):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO players(telegram_id, username, display_name, created_at)
                VALUES(%s, %s, %s, %s)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    display_name = EXCLUDED.display_name
            """, (user.id, user.username, user.full_name, int(time.time())))


def player(tg_id):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM players WHERE telegram_id = %s",
                (tg_id,),
            )
            return cur.fetchone()


def is_admin(tg_id):
    return tg_id in ADMIN_IDS


def menu(tg_id):
    rows = [
        ["🎮 العب", "👤 الملف الشخصي"],
        ["👥 الفرق", "🏆 البطولات"],
        ["🛒 المتجر", "🎒 مخزني"],
        ["📊 التصنيف", "🎁 الدخول اليومي"],
        ["👫 الأصدقاء", "📅 الموسم"],
    ]
    if is_admin(tg_id):
        rows.append(["🛠 لوحة الأدمن"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    ensure_player(update.effective_user)
    p = player(update.effective_user.id)
    if not p:
        await update.message.reply_text("تعذر تحميل ملف اللاعب. حاول مرة ثانية.")
        return
    if p.get("is_banned", False):
        await update.message.reply_text("هذا الحساب موقوف حالياً.")
        return
    await update.message.reply_text(
        "🏟️ أهلاً بك في GUESS ARENA 👑\n"
        "CHALLENGE • GUESS • WIN\n"
        "اختر من القائمة:",
        reply_markup=menu(update.effective_user.id),
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"معرّف تيليجرام الخاص بك: {update.effective_user.id}"
    )


async def profile(update, context):
    p = player(update.effective_user.id)
    if not p:
        await update.message.reply_text("لم أجد ملفك. أرسل /start أولاً.")
        return
    level = max(1, p["xp"] // 500 + 1)
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT t.name, tm.role
                FROM team_members tm
                JOIN teams t ON t.id = tm.team_id
                WHERE tm.player_id = %s
            """, (update.effective_user.id,))
            team = cur.fetchone()
    await update.message.reply_text(
        f"👤 الملف الشخصي\n"
        f"الاسم: {p['display_name'] or 'لاعب'}\n"
        f"النقاط: {p['points']}\n"
        f"XP: {p['xp']}\n"
        f"المستوى: {level}\n"
        f"الانتصارات: {p['wins']}\n"
        f"الخسائر: {p['losses']}\n"
        f"الفريق: {team['name'] if team else 'بدون فريق'}"
    )


async def leaderboard(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT display_name, username, points, wins
                FROM players
                WHERE is_banned = FALSE
                ORDER BY points DESC, wins DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("لا يوجد لاعبون في التصنيف بعد.")
        return
    lines = ["📊 أفضل 10 لاعبين:"]
    for i, p in enumerate(rows, 1):
        lines.append(
            f"{i}. {p['display_name'] or p['username'] or 'لاعب'} — "
            f"{p['points']} نقطة"
        )
    await update.message.reply_text("\n".join(lines))


async def daily(update, context):
    now = int(time.time())
    uid = update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT last_daily FROM players WHERE telegram_id = %s FOR UPDATE",
                (uid,),
            )
            row = cur.fetchone()
            if not row:
                await update.message.reply_text("أرسل /start أولاً.")
                return
            if now - row["last_daily"] < 86400:
                hours = (86400 - (now - row["last_daily"])) // 3600
                await update.message.reply_text(
                    f"استلمت مكافأتك. ارجع بعد {hours} ساعة تقريباً."
                )
                return
            cur.execute(
                "UPDATE players SET xp = xp + 20, last_daily = %s WHERE telegram_id = %s",
                (now, uid),
            )
    await update.message.reply_text("🎁 حصلت على 20 XP!")


async def play(update, context):
    context.user_data["game"] = {
        "number": random.randint(1, 150),
        "attempt": 0,
    }
    await update.message.reply_text(
        "🎮 الوضع العادي: خمن رقماً من 1 إلى 150. لديك 6 محاولات."
    )


async def team_menu(update, context):
    await update.message.reply_text(
        "👥 نظام الفرق\n"
        "لإنشاء فريق أرسل: فريق جديد اسم الفريق\n"
        "للانضمام إلى فريق أرسل: انضم رمز_الدعوة\n"
        "كل لاعب يمكنه الانضمام إلى فريق واحد."
    )


async def create_team(update, context, name):
    uid = update.effective_user.id
    name = name.strip()
    if len(name) < 3 or len(name) > 24:
        await update.message.reply_text(
            "اسم الفريق يجب أن يكون بين 3 و24 حرفاً."
        )
        return
    code = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8].upper()
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM team_members WHERE player_id = %s",
                (uid,),
            )
            if cur.fetchone():
                await update.message.reply_text("أنت منضم إلى فريق بالفعل.")
                return
            cur.execute("""
                INSERT INTO teams(name, invite_code, leader_id, created_at)
                VALUES(%s, %s, %s, %s)
                RETURNING id
            """, (name, code, uid, int(time.time())))
            tid = cur.fetchone()["id"]
            cur.execute("""
                INSERT INTO team_members(team_id, player_id, role, joined_at)
                VALUES(%s, %s, 'leader', %s)
            """, (tid, uid, int(time.time())))
    await update.message.reply_text(
        f"تم إنشاء فريق {name} ✅\n"
        f"رمز الدعوة: {code}\n"
        "احتفظ بالرمز وشاركه مع أعضاء فريقك."
    )


async def join_team(update, context, code):
    uid = update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM team_members WHERE player_id = %s",
                (uid,),
            )
            if cur.fetchone():
                await update.message.reply_text("أنت منضم إلى فريق بالفعل.")
                return
            cur.execute(
                "SELECT * FROM teams WHERE invite_code = %s",
                (code.upper(),),
            )
            team = cur.fetchone()
            if not team:
                await update.message.reply_text("رمز الفريق غير صحيح.")
                return
            cur.execute(
                "SELECT COUNT(*) AS n FROM team_members WHERE team_id = %s",
                (team["id"],),
            )
            if cur.fetchone()["n"] >= 4:
                await update.message.reply_text("الفريق مكتمل (4 لاعبين).")
                return
            cur.execute("""
                INSERT INTO team_members(team_id, player_id, role, joined_at)
                VALUES(%s, %s, 'member', %s)
            """, (team["id"], uid, int(time.time())))
    await update.message.reply_text(f"انضممت إلى فريق {team['name']} ✅")


async def tournaments(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM tournaments
                WHERE status IN ('open', 'active')
                ORDER BY created_at DESC
                LIMIT 15
            """)
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("لا توجد بطولات مفتوحة حالياً.")
        return
    lines = ["🏆 البطولات المتاحة:"]
    for t in rows:
        lines.append(
            f"#{t['id']} — {t['title']} | {t['mode']} | "
            f"الجائزة: {t['prize_points']} نقطة | "
            f"/join_tournament {t['id']}"
        )
    await update.message.reply_text("\n".join(lines))


async def join_tournament(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "استخدم: /join_tournament رقم_البطولة"
        )
        return
    tid = int(context.args[0])
    uid = update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM tournaments WHERE id = %s AND status = 'open' FOR UPDATE",
                (tid,),
            )
            t = cur.fetchone()
            if not t:
                await update.message.reply_text(
                    "البطولة غير موجودة أو التسجيل مغلق."
                )
                return
            cur.execute(
                "SELECT COUNT(*) AS n FROM tournament_entries WHERE tournament_id = %s",
                (tid,),
            )
            if cur.fetchone()["n"] >= t["max_players"]:
                await update.message.reply_text("اكتمل عدد المشاركين.")
                return
            cur.execute("""
                INSERT INTO tournament_entries(tournament_id, player_id, joined_at)
                VALUES(%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (tid, uid, int(time.time())))
    await update.message.reply_text(f"تم تسجيلك في بطولة {t['title']} ✅")


async def shop(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM shop_items WHERE active = TRUE ORDER BY price"
            )
            rows = cur.fetchall()
    lines = ["🛒 المتجر:"]
    for item in rows:
        lines.append(
            f"{item['id']}. {item['title']} — {item['price']} نقطة | "
            f"/buy {item['id']}"
        )
    await update.message.reply_text("\n".join(lines))


async def buy(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /buy رقم_العنصر")
        return
    iid = int(context.args[0])
    uid = update.effective_user.id
    now = int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM shop_items WHERE id = %s AND active = TRUE",
                (iid,),
            )
            item = cur.fetchone()
            if not item:
                await update.message.reply_text("العنصر غير موجود.")
                return
            cur.execute(
                "SELECT points FROM players WHERE telegram_id = %s FOR UPDATE",
                (uid,),
            )
            p = cur.fetchone()
            if not p:
                await update.message.reply_text("أرسل /start أولاً.")
                return
            if p["points"] < item["price"]:
                await update.message.reply_text("نقاطك لا تكفي لشراء هذا العنصر.")
                return
            cur.execute(
                "UPDATE players SET points = points - %s WHERE telegram_id = %s",
                (item["price"], uid),
            )
            expires = (
                now + item["duration_hours"] * 3600
                if item["duration_hours"]
                else None
            )
            cur.execute("""
                INSERT INTO inventory(
                    player_id, item_id, remaining_uses, expires_at, purchased_at
                )
                VALUES(%s, %s, %s, %s, %s)
            """, (uid, iid, item["uses"], expires, now))
    await update.message.reply_text(
        f"تم شراء {item['title']} وإضافته إلى مخزونك ✅"
    )


async def inventory(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT s.title, i.remaining_uses, i.expires_at
                FROM inventory i
                JOIN shop_items s ON s.id = i.item_id
                WHERE i.player_id = %s
                  AND (i.remaining_uses IS NULL OR i.remaining_uses > 0)
                  AND (i.expires_at IS NULL OR i.expires_at > %s)
                ORDER BY i.purchased_at DESC
            """, (update.effective_user.id, int(time.time())))
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("مخزونك فارغ حالياً.")
        return
    lines = ["🎒 مخزني:"]
    for r in rows:
        detail = (
            f"متبقي {r['remaining_uses']} استخدام"
            if r["remaining_uses"] is not None
            else "مدة العنصر 24 ساعة بعد التفعيل (التفعيل قيد الإضافة)"
        )
        lines.append(f"• {r['title']} — {detail}")
    await update.message.reply_text("\n".join(lines))


async def season(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM seasons
                WHERE status = 'active'
                ORDER BY starts_at DESC
                LIMIT 1
            """)
            s = cur.fetchone()
            cur.execute("""
                SELECT name, season_points
                FROM teams
                ORDER BY season_points DESC
                LIMIT 10
            """)
            teams = cur.fetchall()
    if not s:
        await update.message.reply_text("لا يوجد موسم نشط حالياً.")
        return
    lines = [f"📅 {s['title']}", "تصنيف الفرق:"]
    lines += [
        f"{i}. {t['name']} — {t['season_points']}"
        for i, t in enumerate(teams, 1)
    ]
    await update.message.reply_text("\n".join(lines))


async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("هذا القسم مخصص للإدارة فقط.")
        return
    await update.message.reply_text(
        "🛠 لوحة الأدمن\n"
        "/admin_stats — إحصائيات اللاعبين والفرق\n"
        "/admin_create_tournament عنوان|الوصف|الجائزة|الحد_الأقصى\n"
        "/admin_points Telegram_ID مقدار_النقاط\n"
        "/admin_ban Telegram_ID\n"
        "/admin_unban Telegram_ID\n"
        "/admin_season عنوان|عدد_أيام\n"
        "/admin_end_season — إنهاء الموسم وخصم 20% من نقاط اللاعبين"
    )


async def admin_stats(update, context):
    if not is_admin(update.effective_user.id):
        return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM players")
            players_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM teams")
            teams_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM tournaments")
            tournaments_count = cur.fetchone()["n"]
    await update.message.reply_text(
        f"📊 الإحصائيات\n"
        f"اللاعبون: {players_count}\n"
        f"الفرق: {teams_count}\n"
        f"البطولات: {tournaments_count}"
    )


async def admin_create_tournament(update, context):
    if not is_admin(update.effective_user.id):
        return
    raw = " ".join(context.args)
    parts = raw.split("|")
    if len(parts) < 4:
        await update.message.reply_text(
            "الصيغة: /admin_create_tournament عنوان|الوصف|الجائزة|الحد_الأقصى"
        )
        return
    title, desc = parts[0].strip(), parts[1].strip()
    try:
        prize, maxp = int(parts[2]), int(parts[3])
    except ValueError:
        await update.message.reply_text(
            "الجائزة والحد الأقصى يجب أن يكونا أرقاماً."
        )
        return
    if not title or prize < 0 or maxp < 2:
        await update.message.reply_text(
            "تحقق من العنوان والجائزة والحد الأقصى."
        )
        return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO tournaments(
                    title, description, prize_points, max_players, created_by, created_at
                )
                VALUES(%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                title,
                desc,
                prize,
                maxp,
                update.effective_user.id,
                int(time.time()),
            ))
            tid = cur.fetchone()["id"]
    await update.message.reply_text(
        f"تم إنشاء البطولة #{tid} وفتح التسجيل ✅"
    )


async def admin_points(update, context):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "الصيغة: /admin_points Telegram_ID مقدار_النقاط"
        )
        return
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("تأكد من الأرقام.")
        return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                UPDATE players
                SET points = GREATEST(0, points + %s)
                WHERE telegram_id = %s
                RETURNING points
            """, (amount, uid))
            row = cur.fetchone()
    await update.message.reply_text(
        f"تم التعديل. الرصيد الحالي: {row['points']}"
        if row
        else "اللاعب غير موجود."
    )


async def admin_ban_common(update, context, banned):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("اكتب Telegram_ID بعد الأمر.")
        return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                UPDATE players
                SET is_banned = %s
                WHERE telegram_id = %s
                RETURNING telegram_id
            """, (banned, int(context.args[0])))
            row = cur.fetchone()
    await update.message.reply_text(
        "تم تحديث حالة اللاعب." if row else "اللاعب غير موجود."
    )


async def admin_ban(update, context):
    await admin_ban_common(update, context, True)


async def admin_unban(update, context):
    await admin_ban_common(update, context, False)


async def admin_season(update, context):
    if not is_admin(update.effective_user.id):
        return
    parts = " ".join(context.args).split("|")
    if len(parts) != 2:
        await update.message.reply_text(
            "الصيغة: /admin_season عنوان|عدد_أيام"
        )
        return
    try:
        days = int(parts[1])
    except ValueError:
        await update.message.reply_text("عدد الأيام يجب أن يكون رقماً.")
        return
    if days < 1:
        await update.message.reply_text("عدد الأيام يجب أن يكون يوماً واحداً على الأقل.")
        return
    now = int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE seasons SET status = 'ended' WHERE status = 'active'"
            )
            cur.execute("""
                INSERT INTO seasons(title, starts_at, ends_at, status)
                VALUES(%s, %s, %s, 'active')
            """, (parts[0].strip(), now, now + days * 86400))
    await update.message.reply_text("تم إنشاء الموسم الجديد.")


async def admin_end_season(update, context):
    if not is_admin(update.effective_user.id):
        return
    now = int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT id
                FROM seasons
                WHERE status = 'active'
                ORDER BY starts_at DESC
                LIMIT 1
            """)
            s = cur.fetchone()
            if not s:
                await update.message.reply_text("لا يوجد موسم نشط.")
                return
            sid = s["id"]
            cur.execute("""
                INSERT INTO season_deductions(
                    season_id, player_id, deducted_points
                )
                SELECT %s, telegram_id, FLOOR(points * 0.20)::int
                FROM players
                ON CONFLICT(season_id, player_id) DO NOTHING
            """, (sid,))
            cur.execute("""
                UPDATE players p
                SET points = GREATEST(0, p.points - d.deducted_points)
                FROM season_deductions d
                WHERE d.season_id = %s
                  AND d.player_id = p.telegram_id
            """, (sid,))
            cur.execute(
                "UPDATE seasons SET status = 'ended', ends_at = %s WHERE id = %s",
                (now, sid),
            )
    await update.message.reply_text(
        "انتهى الموسم وتم تطبيق خصم 20% مرة واحدة على نقاط اللاعبين."
    )


def friend_guess_keyboard(challenge_id, page=0):
    page = max(0, min(2, int(page)))
    first = page * 50 + 1
    rows = []
    for offset in range(0, 50, 5):
        rows.append([
            InlineKeyboardButton(
                str(n), callback_data=f"fcg:{challenge_id}:{n}"
            )
            for n in range(first + offset, first + offset + 5)
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀️", callback_data=f"fcpage:{challenge_id}:{page - 1}"
        ))
    nav.append(InlineKeyboardButton(f"{page + 1}/3", callback_data="friends:noop"))
    if page < 2:
        nav.append(InlineKeyboardButton(
            "▶️", callback_data=f"fcpage:{challenge_id}:{page + 1}"
        ))
    rows.append(nav)
    rows.append([InlineKeyboardButton("👫 الأصدقاء", callback_data="friends:open")])
    return InlineKeyboardMarkup(rows)


async def show_friends(query, uid):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT telegram_id, username, display_name
                FROM players
                WHERE telegram_id <> %s AND COALESCE(is_banned, FALSE) = FALSE
                ORDER BY created_at DESC, telegram_id DESC
                LIMIT 20
            """, (uid,))
            people = cur.fetchall()
            cur.execute("""
                SELECT COUNT(*) AS n FROM friend_challenges
                WHERE status='pending' AND opponent_id=%s
            """, (uid,))
            pending_count = cur.fetchone()["n"]
    buttons = []
    for person in people:
        label = person["display_name"] or ("@" + person["username"] if person["username"] else str(person["telegram_id"]))
        buttons.append([InlineKeyboardButton(
            f"⚔️ تحدي {label[:28]}",
            callback_data=f"friend:challenge:{person['telegram_id']}"
        )])
    buttons.append([InlineKeyboardButton(
        f"📨 التحديات الواردة ({pending_count})",
        callback_data="friends:pending"
    )])
    buttons.append([InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="friends:home")])
    body = "👫 **تحدي الأصدقاء**\nاختر لاعباً لإرسال تحدٍ له. يجب أن يكون قد فتح البوت مرة واحدة على الأقل."
    if not people:
        body = "لا يوجد لاعبون مسجلون غيرك حالياً. اطلب من صديقك فتح البوت عبر /start أولاً."
    await query.edit_message_text(body, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def friend_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    await q.answer()

    if data == "friends:noop":
        return
    if data == "friends:home":
        await q.edit_message_text("اختر من القائمة الرئيسية عبر /start.")
        return
    if data == "friends:open":
        await show_friends(q, uid)
        return

    if data == "friends:pending":
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT c.id, c.challenger_id, p.display_name, p.username
                    FROM friend_challenges c
                    JOIN players p ON p.telegram_id=c.challenger_id
                    WHERE c.opponent_id=%s AND c.status='pending'
                    ORDER BY c.created_at DESC LIMIT 20
                """, (uid,))
                incoming = cur.fetchall()
                cur.execute("""
                    SELECT c.id, p.display_name, p.username
                    FROM friend_challenges c
                    JOIN players p ON p.telegram_id=c.opponent_id
                    WHERE c.challenger_id=%s AND c.status='pending'
                    ORDER BY c.created_at DESC LIMIT 10
                """, (uid,))
                outgoing = cur.fetchall()
        buttons = []
        for row in incoming:
            name = row["display_name"] or ("@" + row["username"] if row["username"] else "لاعب")
            buttons.append([
                InlineKeyboardButton(f"✅ قبول {name[:18]}", callback_data=f"friend:accept:{row['id']}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"friend:reject:{row['id']}")
            ])
        buttons.append([InlineKeyboardButton("⬅️ رجوع للأصدقاء", callback_data="friends:open")])
        lines = ["📨 **التحديات الواردة**"]
        if not incoming:
            lines.append("لا توجد تحديات واردة حالياً.")
        if outgoing:
            lines.append("\n⏳ تحديات أرسلتها وبانتظار الرد:")
            for row in outgoing:
                name = row["display_name"] or ("@" + row["username"] if row["username"] else "لاعب")
                lines.append(f"• {name}")
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return

    if data.startswith("friend:challenge:"):
        try:
            opponent_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await q.edit_message_text("معرّف اللاعب غير صحيح.")
            return
        if opponent_id == uid:
            await q.edit_message_text("لا يمكنك تحدي نفسك.")
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT telegram_id, is_banned FROM players WHERE telegram_id=%s", (opponent_id,))
                opponent = cur.fetchone()
                if not opponent or opponent["is_banned"]:
                    await q.edit_message_text("هذا اللاعب غير متاح للتحدي.")
                    return
                cur.execute("""
                    SELECT id FROM friend_challenges
                    WHERE status IN ('pending','active')
                      AND ((challenger_id=%s AND opponent_id=%s)
                        OR (challenger_id=%s AND opponent_id=%s))
                    LIMIT 1
                """, (uid, opponent_id, opponent_id, uid))
                existing = cur.fetchone()
                if existing:
                    await q.edit_message_text("يوجد تحدٍ قائم بينكما بالفعل.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📨 تحدياتي", callback_data="friends:pending")]]))
                    return
                cur.execute("""
                    INSERT INTO friend_challenges(challenger_id, opponent_id, secret_number, status, created_at)
                    VALUES(%s,%s,%s,'pending',%s) RETURNING id
                """, (uid, opponent_id, random.randint(1, 150), int(time.time())))
                challenge_id = cur.fetchone()["id"]
        try:
            sender = q.from_user.full_name or "لاعب"
            await context.bot.send_message(
                chat_id=opponent_id,
                text=f"⚔️ وصلك تحدي تخمين من {sender}!\nاقبل التحدي لتبدأ المباراة.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ قبول التحدي", callback_data=f"friend:accept:{challenge_id}"),
                    InlineKeyboardButton("❌ رفض", callback_data=f"friend:reject:{challenge_id}")
                ]])
            )
        except Exception:
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE friend_challenges SET status='cancelled', finished_at=%s WHERE id=%s AND status='pending'", (int(time.time()), challenge_id))
            await q.edit_message_text("لم أستطع مراسلة اللاعب. يجب أن يفتح البوت ويرسل /start أولاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ الأصدقاء", callback_data="friends:open")]]))
            return
        await q.edit_message_text("✅ تم إرسال التحدي. بانتظار قبول صديقك.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📨 تحدياتي", callback_data="friends:pending")], [InlineKeyboardButton("⬅️ الأصدقاء", callback_data="friends:open")]]))
        return

    if data.startswith("friend:reject:"):
        try:
            challenge_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""
                    UPDATE friend_challenges SET status='rejected', finished_at=%s
                    WHERE id=%s AND opponent_id=%s AND status='pending'
                    RETURNING challenger_id
                """, (int(time.time()), challenge_id, uid))
                row = cur.fetchone()
        if row:
            try:
                await context.bot.send_message(row["challenger_id"], "تم رفض تحديك من اللاعب.")
            except Exception:
                pass
            await q.edit_message_text("تم رفض التحدي.")
        else:
            await q.edit_message_text("هذا التحدي غير متاح أو لا يخصك.")
        return

    if data.startswith("friend:accept:"):
        try:
            challenge_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM friend_challenges WHERE id=%s FOR UPDATE", (challenge_id,))
                row = cur.fetchone()
                if not row or row["opponent_id"] != uid or row["status"] != "pending":
                    row = None
                else:
                    cur.execute("""
                        UPDATE friend_challenges SET status='active', current_turn=challenger_id
                        WHERE id=%s
                    """, (challenge_id,))
        if not row:
            await q.edit_message_text("هذا التحدي انتهى أو لا يخصك.")
            return
        try:
            await context.bot.send_message(
                row["challenger_id"],
                f"✅ قبل صديقك التحدي #{challenge_id}. دورك أولاً! اختر رقمك من 1 إلى 150.",
                reply_markup=friend_guess_keyboard(challenge_id, 0)
            )
        except Exception:
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE friend_challenges SET status='cancelled', finished_at=%s WHERE id=%s AND status='active'", (int(time.time()), challenge_id))
            await q.edit_message_text("تعذر بدء المباراة لأن الطرف الآخر لم يعد يستقبل الرسائل.")
            return
        await q.edit_message_text("🎮 قبلت التحدي! أرسلت للاعب الآخر أن دوره يبدأ الآن. انتظر دورك.")
        return

    if data.startswith("fcpage:"):
        try:
            _, challenge_raw, page_raw = data.split(":")
            challenge_id, page = int(challenge_raw), int(page_raw)
        except ValueError:
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM friend_challenges WHERE id=%s AND status='active'", (challenge_id,))
                game = cur.fetchone()
        if not game or game["current_turn"] != uid:
            await q.edit_message_text("هذا ليس دورك أو أن المباراة انتهت.")
            return
        await q.edit_message_text(f"🎯 دورك في التحدي #{challenge_id}. اختر رقماً من 1 إلى 150.", reply_markup=friend_guess_keyboard(challenge_id, page))
        return

    if data.startswith("fcg:"):
        try:
            _, challenge_raw, guess_raw = data.split(":")
            challenge_id, guess = int(challenge_raw), int(guess_raw)
        except ValueError:
            return
        if not 1 <= guess <= 150:
            return
        result = None
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM friend_challenges WHERE id=%s FOR UPDATE", (challenge_id,))
                game = cur.fetchone()
                if not game or game["status"] != "active" or game["current_turn"] != uid:
                    game = None
                else:
                    challenger = game["challenger_id"]
                    opponent = game["opponent_id"]
                    is_challenger = uid == challenger
                    col = "challenger_attempts" if is_challenger else "opponent_attempts"
                    attempts = game[col] + 1
                    total_attempts = game["challenger_attempts"] + game["opponent_attempts"] + 1
                    now = int(time.time())
                    cur.execute(f"UPDATE friend_challenges SET {col}=%s WHERE id=%s", (attempts, challenge_id))
                    if guess == game["secret_number"]:
                        winner = uid
                        loser = opponent if uid == challenger else challenger
                        cur.execute("UPDATE friend_challenges SET status='finished',finished_at=%s WHERE id=%s", (now, challenge_id))
                        cur.execute("UPDATE players SET points=points+10,xp=xp+100,wins=wins+1 WHERE telegram_id=%s", (winner,))
                        cur.execute("UPDATE players SET losses=losses+1 WHERE telegram_id=%s", (loser,))
                        cur.execute("INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at) VALUES(%s,'win',%s,10,100,%s)", (winner, attempts, now))
                        loser_attempts = game["opponent_attempts"] if is_challenger else game["challenger_attempts"]
                        cur.execute("INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at) VALUES(%s,'loss',%s,0,0,%s)", (loser, loser_attempts, now))
                        result = ("win", winner, loser, attempts)
                    elif total_attempts >= 12:
                        cur.execute("UPDATE friend_challenges SET status='draw',finished_at=%s WHERE id=%s", (now, challenge_id))
                        result = ("draw", opponent if uid == challenger else challenger, None, attempts)
                    else:
                        next_turn = opponent if uid == challenger else challenger
                        cur.execute("UPDATE friend_challenges SET current_turn=%s WHERE id=%s", (next_turn, challenge_id))
                        next_attempts = game["opponent_attempts"] if next_turn == opponent else game["challenger_attempts"]
                        result = ("continue", next_turn, None, attempts, next_attempts)
        if not game:
            await q.edit_message_text("هذا ليس دورك أو أن المباراة انتهت.")
            return
        hint = "أكبر 🔼" if guess < game["secret_number"] else "أصغر 🔽"
        if result[0] == "win":
            await q.edit_message_text("🏆 صحيح! فزت بالتحدي وربحت 10 نقاط و100 XP!")
            try:
                await context.bot.send_message(result[2], "انتهت المباراة. خصمك خمّن الرقم وفاز عليك.")
            except Exception:
                pass
            return
        if result[0] == "draw":
            await q.edit_message_text("🤝 انتهت المباراة بالتعادل! لم تتم إضافة أو خصم نقاط.")
            try:
                await context.bot.send_message(result[1], "🤝 انتهت مباراتكما بالتعادل! لم تتم إضافة أو خصم نقاط.")
            except Exception:
                pass
            return
        next_turn, next_attempts = result[1], result[4]
        try:
            await context.bot.send_message(
                next_turn,
                f"🎯 دورك الآن في التحدي #{challenge_id}.\nتخمين الخصم: {guess} — {hint}\nمحاولاتك: {next_attempts}/6",
                reply_markup=friend_guess_keyboard(challenge_id, 0)
            )
        except Exception:
            pass
        await q.edit_message_text(f"تم تسجيل تخمينك: {guess}. {hint}\nتم تحويل الدور إلى خصمك.")
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    p = player(uid)
    if p and p.get("is_banned", False):
        await update.message.reply_text("هذا الحساب موقوف حالياً.")
        return

    text = update.message.text.strip()
    if text == "🎮 العب":
        await play(update, context)
        return
    if text == "👤 الملف الشخصي":
        await profile(update, context)
        return
    if text == "📊 التصنيف":
        await leaderboard(update, context)
        return
    if text == "🎁 الدخول اليومي":
        await daily(update, context)
        return
    if text == "👥 الفرق":
        await team_menu(update, context)
        return
    if text == "🏆 البطولات":
        await tournaments(update, context)
        return
    if text == "🛒 المتجر":
        await shop(update, context)
        return
    if text == "🎒 مخزني":
        await inventory(update, context)
        return
    if text == "📅 الموسم":
        await season(update, context)
        return
    if text == "🛠 لوحة الأدمن":
        await admin_panel(update, context)
        return
    if text == "👫 الأصدقاء":
        # Open the interactive friends challenge menu.
        class _MessageQuery:
            def __init__(self, message):
                self.message = message
            async def edit_message_text(self, text, **kwargs):
                await self.message.reply_text(text, **kwargs)
        await show_friends(_MessageQuery(update.message), uid)
        return
    if text.startswith("فريق جديد "):
        await create_team(update, context, text[len("فريق جديد "):])
        return
    if text.startswith("انضم "):
        await join_team(update, context, text[len("انضم "):].strip())
        return

    game = context.user_data.get("game")
    if game:
        try:
            guess = int(text)
        except ValueError:
            await update.message.reply_text("أرسل رقماً من 1 إلى 150.")
            return
        if not 1 <= guess <= 150:
            await update.message.reply_text("الرقم يجب أن يكون بين 1 و150.")
            return

        game["attempt"] += 1
        n = game["attempt"]
        if guess == game["number"]:
            pts = [10, 8, 6, 4, 2, 1][n - 1]
            xp = [100, 85, 70, 60, 50, 40][n - 1]
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        UPDATE players
                        SET points = points + %s,
                            xp = xp + %s,
                            wins = wins + 1
                        WHERE telegram_id = %s
                    """, (pts, xp, uid))
            context.user_data.pop("game", None)
            await update.message.reply_text(
                f"صحيح! ربحت {pts} نقطة و{xp} XP 🎉"
            )
            return

        if n >= 6:
            answer = game["number"]
            context.user_data.pop("game", None)
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute(
                        "UPDATE players SET losses = losses + 1 WHERE telegram_id = %s",
                        (uid,),
                    )
            await update.message.reply_text(
                f"انتهت المحاولات. الرقم كان {answer}."
            )
            return

        await update.message.reply_text(
            ("أكبر 🔼" if guess < game["number"] else "أصغر 🔽")
            + f"\nباقي {6 - n} محاولات."
        )
        return

    await update.message.reply_text(
        "اختر من القائمة أو اكتب /start.",
        reply_markup=menu(uid),
    )


async def error_handler(update, context):
    log.exception("Unhandled Telegram update error", exc_info=context.error)


def main():
    init_db()
    start_health_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("join_tournament", join_tournament))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("admin_create_tournament", admin_create_tournament))
    app.add_handler(CommandHandler("admin_points", admin_points))
    app.add_handler(CommandHandler("admin_ban", admin_ban))
    app.add_handler(CommandHandler("admin_unban", admin_unban))
 
