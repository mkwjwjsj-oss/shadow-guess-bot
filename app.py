# GUESS ARENA build: 2026-11-24-fix-joined-check
import os
import time
import random
import logging
import secrets

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
            for statement in (
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS username TEXT",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS display_name TEXT",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS xp INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS wins INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS losses INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS last_daily BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT 0",
            ):
                cur.execute(statement)

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
                CREATE TABLE IF NOT EXISTS friend_challenges (
                    id BIGSERIAL PRIMARY KEY,
                    challenger_id BIGINT NOT NULL REFERENCES players(telegram_id),
                    opponent_id BIGINT NOT NULL REFERENCES players(telegram_id),
                    secret_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    current_turn BIGINT,
                    challenger_attempts INTEGER NOT NULL DEFAULT 0,
                    opponent_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL,
                    finished_at BIGINT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_friend_challenges_players
                ON friend_challenges(challenger_id, opponent_id, status)
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
            cur.execute("SELECT * FROM players WHERE telegram_id = %s", (tg_id,))
            return cur.fetchone()


def is_admin(tg_id):
    return tg_id in ADMIN_IDS


def menu(tg_id):
    rows = [
        [InlineKeyboardButton("🎮 العب", callback_data="m:play"),
         InlineKeyboardButton("👤 الملف الشخصي", callback_data="m:profile")],
        [InlineKeyboardButton("👥 الفرق", callback_data="m:teams"),
         InlineKeyboardButton("🏆 البطولات", callback_data="m:tournaments")],
        [InlineKeyboardButton("🛒 المتجر", callback_data="m:shop"),
         InlineKeyboardButton("🎒 مخزني", callback_data="m:inventory")],
        [InlineKeyboardButton("📊 التصنيف", callback_data="m:rank"),
         InlineKeyboardButton("🎁 الدخول اليومي", callback_data="m:daily")],
        [InlineKeyboardButton("👫 الأصدقاء", callback_data="m:friends"),
         InlineKeyboardButton("📅 الموسم", callback_data="m:season")],
    ]
    if is_admin(tg_id):
        rows.append([InlineKeyboardButton("🛠 لوحة الأدمن", callback_data="m:admin")])
    return InlineKeyboardMarkup(rows)


def back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="m:home")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats")],
        [InlineKeyboardButton("⭐ إعطاء نقاط", callback_data="admin:players:points:0")],
        [InlineKeyboardButton("🚫 حظر لاعب", callback_data="admin:players:ban:0"),
         InlineKeyboardButton("🔓 فك الحظر", callback_data="admin:players:unban:0")],
        [InlineKeyboardButton("📅 إنشاء موسم", callback_data="admin:season_create")],
        [InlineKeyboardButton("🏁 إنهاء الموسم", callback_data="admin:end")],
        [InlineKeyboardButton("🏆 إنشاء بطولة", callback_data="admin:tournament_create")],
        [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="m:home")],
    ])


async def show_admin_players(q, action, page=0):
    page = max(0, page)
    offset = page * 8
    where = ""
    if action == "ban":
        where = "WHERE is_banned = FALSE"
    elif action == "unban":
        where = "WHERE is_banned = TRUE"

    with connect() as con:
        with con.cursor() as cur:
            cur.execute(f"""
                SELECT telegram_id, display_name, username
                FROM players {where}
                ORDER BY created_at DESC
                LIMIT 8 OFFSET %s
            """, (offset,))
            rows = cur.fetchall()

    buttons = []
    for row in rows:
        name = row["display_name"] or row["username"] or "لاعب"
        buttons.append([InlineKeyboardButton(
            f"{name[:18]} · {row['telegram_id']}",
            callback_data=f"admin:select:{action}:{row['telegram_id']}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀️ السابق", callback_data=f"admin:players:{action}:{page - 1}"
        ))
    if len(rows) == 8:
        nav.append(InlineKeyboardButton(
            "التالي ▶️", callback_data=f"admin:players:{action}:{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ لوحة الأدمن", callback_data="admin:home")])

    labels = {
        "points": "اختر اللاعب لإعطائه أو خصم نقاط:",
        "ban": "اختر اللاعب الذي تريد حظره:",
        "unban": "اختر اللاعب الذي تريد فك حظره:",
    }
    await q.edit_message_text(
        labels[action] if rows else "لا يوجد لاعبون في هذه القائمة.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def render_guess(q, context, page=0, notice=None):
    game = context.user_data.get("game")
    if not game:
        await q.edit_message_text("لا توجد جولة نشطة.", reply_markup=back())
        return
    page = max(0, min(9, page))
    game["page"] = page
    first = page * 15 + 1
    rows = []
    for offset in range(0, 15, 3):
        rows.append([
            InlineKeyboardButton(str(n), callback_data=f"guess:{n}")
            for n in range(first + offset, first + offset + 3)
        ])
    nav = []
    if page:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"guesspage:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/10", callback_data="noop"))
    if page < 9:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"guesspage:{page+1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("❌ إنهاء الجولة", callback_data="game:cancel"),
        InlineKeyboardButton("القائمة", callback_data="m:home")
    ])
    body = f"🎮 اختر رقماً من 1 إلى 150\nالمحاولات: {game['attempt']}/6"
    if notice:
        body += "\n" + notice
    await q.edit_message_text(body, reply_markup=InlineKeyboardMarkup(rows))


def friend_guess_keyboard(challenge_id, page=0):
    page = max(0, min(2, page))
    first = page * 50 + 1
    rows = []
    for offset in range(0, 50, 5):
        rows.append([InlineKeyboardButton(str(n), callback_data=f"fcg:{challenge_id}:{n}")
                     for n in range(first + offset, min(first + offset + 5, first + 50))])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"fcpage:{challenge_id}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/3", callback_data="noop"))
    if page < 2:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"fcpage:{challenge_id}:{page+1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid, data = q.from_user.id, q.data or ""

    # إدارة البوت: لا يتم تنفيذ أي عملية إدارية إلا بعد فحص هوية الأدمن.
    if data.startswith("admin:"):
        if not is_admin(uid):
            await q.edit_message_text("هذا القسم للإدارة فقط.")
            return

        if data == "admin:home":
            await q.edit_message_text("🛠 لوحة الأدمن", reply_markup=admin_keyboard())
            return

        if data == "admin:stats":
            with connect() as con:
                with con.cursor() as cur:
                    counts = []
                    for table in ("players", "teams", "tournaments"):
                        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                        counts.append(cur.fetchone()["n"])
                    cur.execute("SELECT COUNT(*) AS n FROM players WHERE is_banned=TRUE")
                    banned = cur.fetchone()["n"]
            await q.edit_message_text(
                f"📊 الإحصائيات\nاللاعبون: {counts[0]}\nالفرق: {counts[1]}\n"
                f"البطولات: {counts[2]}\nالحسابات المحظورة: {banned}",
                reply_markup=admin_keyboard()
            )
            return

        if data.startswith("admin:players:"):
            _, _, action, page = data.split(":")
            if action not in ("points", "ban", "unban"):
                return
            await show_admin_players(q, action, int(page))
            return

        if data.startswith("admin:select:"):
            _, _, action, target_id = data.split(":")
            target_id = int(target_id)
            if action == "points":
                target = player(target_id)
                if not target:
                    await q.edit_message_text("اللاعب غير موجود.", reply_markup=admin_keyboard())
                    return
                context.user_data["admin_action"] = {
                    "type": "points", "target_id": target_id
                }
                await q.edit_message_text(
                    f"اللاعب: {target['display_name'] or target['username'] or target_id}\n"
                    f"رصيده الحالي: {target['points']}\n\n"
                    "أرسل مقدار النقاط في رسالة.\n"
                    "مثال: 100 للإضافة أو -50 للخصم.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("إلغاء", callback_data="admin:home")
                    ]])
                )
                return

            if action in ("ban", "unban"):
                banned = action == "ban"
                with connect() as con:
                    with con.cursor() as cur:
                        cur.execute("""
                            UPDATE players SET is_banned=%s
                            WHERE telegram_id=%s RETURNING display_name, username
                        """, (banned, target_id))
                        target = cur.fetchone()
                if not target:
                    msg = "اللاعب غير موجود."
                else:
                    msg = "تم حظر اللاعب 🚫" if banned else "تم فك حظر اللاعب 🔓"
                await q.edit_message_text(msg, reply_markup=admin_keyboard())
                return

        if data == "admin:season_create":
            context.user_data["admin_action"] = {"type": "season"}
            await q.edit_message_text(
                "أرسل اسم الموسم وعدد الأيام بهذا الشكل:\n"
                "الموسم الأول|30",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("إلغاء", callback_data="admin:home")
                ]])
            )
            return

        if data == "admin:tournament_create":
            context.user_data["admin_action"] = {"type": "tournament"}
            await q.edit_message_text(
                "أرسل بيانات البطولة بهذا الشكل:\n"
                "اسم البطولة|الوصف|الجائزة|الحد الأقصى\n"
                "مثال:\nبطولة الأبطال|بطولة عامة|500|32",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("إلغاء", callback_data="admin:home")
                ]])
            )
            return

        if data == "admin:end":
            now = int(time.time())
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT id FROM seasons
                        WHERE status='active'
                        ORDER BY starts_at DESC LIMIT 1 FOR UPDATE
                    """)
                    season_row = cur.fetchone()
                    if not season_row:
                        await q.edit_message_text("لا يوجد موسم نشط.", reply_markup=admin_keyboard())
                        return
                    sid = season_row["id"]
                    cur.execute("""
                        WITH added AS (
                            INSERT INTO season_deductions(season_id,player_id,deducted_points)
                            SELECT %s, telegram_id, FLOOR(points*0.20)::int
                            FROM players
                            ON CONFLICT(season_id,player_id) DO NOTHING
                            RETURNING player_id,deducted_points
                        )
                        UPDATE players p
                        SET points=GREATEST(0,p.points-a.deducted_points)
                        FROM added a
                        WHERE p.telegram_id=a.player_id
                    """, (sid,))
                    cur.execute(
                        "UPDATE seasons SET status='ended',ends_at=%s WHERE id=%s",
                        (now, sid)
                    )
            await q.edit_message_text(
                "انتهى الموسم وتم تطبيق الخصم مرة واحدة.",
                reply_markup=admin_keyboard()
            )
            return

        return

    p = player(uid)
    if p and p.get("is_banned", False):
        await q.edit_message_text("هذا الحساب موقوف حالياً.")
        return

    if data == "noop":
        return

    if data == "m:home":
        context.user_data.pop("game", None)
        await q.edit_message_text("🏟️ GUESS ARENA 👑\nاختر من الأزرار:", reply_markup=menu(uid))
        return

    if data.startswith("m:"):
        action = data[2:]
        if action == "play":
            context.user_data["game"] = {
                "number": random.randint(1, 150), "attempt": 0, "page": 0
            }
            await render_guess(q, context, 0)
            return
        if action == "profile":
            p = player(uid)
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT t.name FROM team_members tm
                        JOIN teams t ON t.id=tm.team_id WHERE tm.player_id=%s
                    """, (uid,))
                    team = cur.fetchone()
            level = max(1, p["xp"] // 500 + 1)
            await q.edit_message_text(
                f"👤 الملف الشخصي\nالاسم: {p['display_name'] or 'لاعب'}\n"
                f"النقاط: {p['points']}\nXP: {p['xp']}\nالمستوى: {level}\n"
                f"الانتصارات: {p['wins']}\nالخسائر: {p['losses']}\n"
                f"الفريق: {team['name'] if team else 'بدون فريق'}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📜 سجل مبارياتي", callback_data="m:history")],
                    [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="m:home")]
                ])
            )
            return
        if action == "history":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT result, attempts, points_earned, xp_earned, played_at
                        FROM match_history WHERE player_id=%s
                        ORDER BY played_at DESC, id DESC LIMIT 10
                    """, (uid,))
                    history = cur.fetchall()
                    cur.execute("""
                        SELECT COUNT(*) AS total,
                               COUNT(*) FILTER (WHERE result='win') AS wins,
                               COUNT(*) FILTER (WHERE result='loss') AS losses,
                               COALESCE(SUM(points_earned),0) AS points,
                               COALESCE(SUM(xp_earned),0) AS xp
                        FROM match_history WHERE player_id=%s
                    """, (uid,))
                    totals = cur.fetchone()
            lines = [
                "📜 سجل آخر 10 مباريات",
                f"إجمالي الجولات: {totals['total']} | فوز: {totals['wins']} | خسارة: {totals['losses']}",
                f"مجموع النقاط المكتسبة: {totals['points']} | XP: {totals['xp']}",
                ""
            ]
            for i, match in enumerate(history, 1):
                status = "🏆 فوز" if match["result"] == "win" else "❌ خسارة"
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(match["played_at"]))
                lines.append(
                    f"{i}. {status} — {match['attempts']} محاولات — "
                    f"+{match['points_earned']} نقطة / +{match['xp_earned']} XP — {stamp}"
                )
            if not history:
                lines.append("ما لعبت أي جولة لحد الآن.")
            await q.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👤 الملف الشخصي", callback_data="m:profile")],
                    [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="m:home")]
                ])
            )
            return
        if action == "rank":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT display_name,username,points FROM players
                        WHERE is_banned=FALSE ORDER BY points DESC,wins DESC LIMIT 10
                    """)
                    rows = cur.fetchall()
            body = "📊 أفضل 10 لاعبين:\n" + (
                "\n".join(
                    f"{i}. {r['display_name'] or r['username'] or 'لاعب'} — {r['points']} نقطة"
                    for i, r in enumerate(rows, 1)
                ) if rows else "لا يوجد لاعبون بعد."
            )
            await q.edit_message_text(body, reply_markup=back())
            return
        if action == "daily":
            now = int(time.time())
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute(
                        "SELECT last_daily FROM players WHERE telegram_id=%s FOR UPDATE",
                        (uid,)
                    )
                    row = cur.fetchone()
                    if not row:
                        body = "ابدأ أولاً بـ /start."
                    elif now - row["last_daily"] < 86400:
                        body = f"🎁 ارجع بعد {max(1,(86400-(now-row['last_daily']))//3600)} ساعة تقريباً."
                    else:
                        cur.execute(
                            "UPDATE players SET xp=xp+20,last_daily=%s WHERE telegram_id=%s",
                            (now, uid)
                        )
                        body = "🎁 حصلت على 20 XP!"
            await q.edit_message_text(body, reply_markup=back())
            return
        if action == "teams":
            await q.edit_message_text("👥 نظام الفرق", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✨ إنشاء فريق باسم جاهز", callback_data="team:create")],
                [InlineKeyboardButton("🤝 الانضمام لفريق", callback_data="team:list")],
                [InlineKeyboardButton("⬅️ القائمة", callback_data="m:home")]
            ]))
            return
        if action == "tournaments":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT id,title,prize_points FROM tournaments
                        WHERE status='open' ORDER BY created_at DESC LIMIT 15
                    """)
                    rows = cur.fetchall()
            buttons = [[InlineKeyboardButton(
                f"🏆 {r['title']} · {r['prize_points']} نقطة",
                callback_data=f"tournament:{r['id']}"
            )] for r in rows]
            buttons.append([InlineKeyboardButton("⬅️ القائمة", callback_data="m:home")])
            await q.edit_message_text(
                "اختر بطولة للتسجيل:" if rows else "لا توجد بطولات مفتوحة.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
        if action == "shop":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("SELECT id,title,price FROM shop_items WHERE active=TRUE ORDER BY price")
                    rows = cur.fetchall()
            buttons = [[InlineKeyboardButton(
                f"🛒 {r['title']} — {r['price']} نقطة", callback_data=f"buy:{r['id']}"
            )] for r in rows]
            buttons.append([InlineKeyboardButton("⬅️ القائمة", callback_data="m:home")])
            await q.edit_message_text("اختر عنصراً للشراء:", reply_markup=InlineKeyboardMarkup(buttons))
            return
        if action == "inventory":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT s.title,i.remaining_uses FROM inventory i
                        JOIN shop_items s ON s.id=i.item_id
                        WHERE i.player_id=%s
                        AND (i.remaining_uses IS NULL OR i.remaining_uses>0)
                        AND (i.expires_at IS NULL OR i.expires_at>%s)
                        ORDER BY i.purchased_at DESC
                    """, (uid, int(time.time())))
                    rows = cur.fetchall()
            body = ("🎒 مخزني:\n" + "\n".join(
                f"• {r['title']} — {r['remaining_uses'] if r['remaining_uses'] is not None else 'بانتظار التفعيل'}"
                for r in rows
            )) if rows else "مخزونك فارغ."
            await q.edit_message_text(body, reply_markup=back())
            return
        if action == "season":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("SELECT title FROM seasons WHERE status='active' ORDER BY starts_at DESC LIMIT 1")
                    season_row = cur.fetchone()
                    cur.execute("SELECT name,season_points FROM teams ORDER BY season_points DESC LIMIT 10")
                    teams = cur.fetchall()
            body = (
                "📅 " + season_row["title"] + "\n" +
                "\n".join(f"{i}. {t['name']} — {t['season_points']}" for i, t in enumerate(teams, 1))
            ) if season_row else "لا يوجد موسم نشط حالياً."
            await q.edit_message_text(body, reply_markup=back())
            return
        if action == "friends":
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT id, challenger_id, opponent_id, status
                        FROM friend_challenges
                        WHERE (challenger_id=%s OR opponent_id=%s)
                          AND status IN ('pending','active')
                        ORDER BY created_at DESC LIMIT 10
                    """, (uid, uid))
                    active_challenges = cur.fetchall()
                    cur.execute("""
                        SELECT telegram_id, display_name, username
                        FROM players
                        WHERE telegram_id<>%s AND is_banned=FALSE
                        ORDER BY created_at DESC LIMIT 20
                    """, (uid,))
                    candidates = cur.fetchall()
            buttons = []
            if active_challenges:
                buttons.append([InlineKeyboardButton("📨 تحدياتك الحالية", callback_data="friends:pending")])
            for person in candidates:
                label = person['display_name'] or ("@" + person['username'] if person['username'] else str(person['telegram_id']))
                buttons.append([InlineKeyboardButton(f"⚔️ تحدي {label[:24]}", callback_data=f"friend:challenge:{person['telegram_id']}")])
            buttons.append([InlineKeyboardButton("📨 طلباتي وتحدياتي", callback_data="friends:pending")])
            buttons.append([InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="m:home")])
            await q.edit_message_text("👫 **تحدي الأصدقاء**\nاختر لاعباً لإرسال تحدي تخمين خاص إليه. أول لاعب يخمن الرقم يفوز.", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
            return
        if action == "admin":
            if not is_admin(uid):
                await q.edit_message_text("هذا القسم للإدارة فقط.")
                return
            await q.edit_message_text("🛠 لوحة الأدمن", reply_markup=admin_keyboard())
            return

    if data == "friends:pending":
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT id, challenger_id, opponent_id, status
                    FROM friend_challenges
                    WHERE (challenger_id=%s OR opponent_id=%s)
                      AND status IN ('pending','active')
                    ORDER BY created_at DESC LIMIT 15
                """, (uid, uid))
                rows = cur.fetchall()
        buttons = []
        lines = ["📨 طلباتك وتحدياتك:"]
        for r in rows:
            other = r['opponent_id'] if r['challenger_id'] == uid else r['challenger_id']
            other_p = player(other)
            name = (other_p['display_name'] or other_p['username'] or str(other)) if other_p else str(other)
            if r['status'] == 'pending' and r['opponent_id'] == uid:
                lines.append(f"طلب تحدي من {name}")
                buttons.append([InlineKeyboardButton(f"✅ قبول {name[:18]}", callback_data=f"friend:accept:{r['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"friend:reject:{r['id']}")])
            elif r['status'] == 'pending':
                lines.append(f"بانتظار رد {name} على تحديك")
            else:
                lines.append(f"مباراة نشطة ضد {name} — الدور: {'أنت' if r['current_turn']==uid else name}")
        buttons.append([InlineKeyboardButton("⬅️ رجوع للأصدقاء", callback_data="m:friends")])
        await q.edit_message_text("\n".join(lines) if rows else "لا توجد طلبات أو تحديات نشطة.", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("friend:challenge:"):
        target = int(data.rsplit(":", 1)[1])
        if target == uid:
            await q.edit_message_text("ما بتقدر تتحدى نفسك.", reply_markup=back())
            return
        target_player = player(target)
        if not target_player or target_player.get('is_banned'):
            await q.edit_message_text("اللاعب غير موجود أو موقوف.", reply_markup=back())
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM friend_challenges
                    WHERE status IN ('pending','active')
                      AND (challenger_id IN (%s,%s) OR opponent_id IN (%s,%s))
                    LIMIT 1
                """, (uid, target, uid, target))
                busy = cur.fetchone()
                if busy:
                    await q.edit_message_text("أنت أو اللاعب الآخر لديكما تحدٍ معلّق أو مباراة نشطة بالفعل.", reply_markup=back())
                    return
                cur.execute("""
                    INSERT INTO friend_challenges(challenger_id,opponent_id,secret_number,status,created_at)
                    VALUES(%s,%s,%s,'pending',%s) RETURNING id
                """, (uid, target, random.randint(1,150), int(time.time())))
                challenge_id = cur.fetchone()['id']
        challenger_name = q.from_user.full_name
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"⚔️ وصلك تحدي تخمين من {challenger_name}!\nالرقم بين 1 و150، ولكل لاعب 6 محاولات بالتناوب. أول من يصيب الرقم يفوز.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ قبول التحدي", callback_data=f"friend:accept:{challenge_id}"), InlineKeyboardButton("❌ رفض", callback_data=f"friend:reject:{challenge_id}")]])
            )
        except Exception:
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE friend_challenges SET status='cancelled',finished_at=%s WHERE id=%s", (int(time.time()), challenge_id))
            await q.edit_message_text("ما قدرت أوصل الدعوة. لازم اللاعب يفتح البوت ويرسل /start أولاً.", reply_markup=back())
            return
        await q.edit_message_text("تم إرسال التحدي ✅\nبانتظار قبول اللاعب.", reply_markup=back())
        return

    if data.startswith("friend:reject:"):
        challenge_id = int(data.rsplit(":", 1)[1])
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""UPDATE friend_challenges SET status='rejected',finished_at=%s
                               WHERE id=%s AND opponent_id=%s AND status='pending' RETURNING challenger_id""", (int(time.time()), challenge_id, uid))
                row = cur.fetchone()
        if row:
            try:
                await context.bot.send_message(row['challenger_id'], "تم رفض تحديك من اللاعب.")
            except Exception:
                pass
            await q.edit_message_text("تم رفض التحدي.", reply_markup=back())
        else:
            await q.edit_message_text("الطلب غير متاح أو تمت معالجته.", reply_markup=back())
        return

    if data.startswith("friend:accept:"):
        challenge_id = int(data.rsplit(":", 1)[1])
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("""UPDATE friend_challenges SET status='active',current_turn=challenger_id
                               WHERE id=%s AND opponent_id=%s AND status='pending'
                               RETURNING challenger_id,opponent_id""", (challenge_id, uid))
                row = cur.fetchone()
        if not row:
            await q.edit_message_text("التحدي انتهى أو تمت معالجته مسبقاً.", reply_markup=back())
            return
        try:
            await context.bot.send_message(row['challenger_id'], "✅ تم قبول تحديك! بدأت المباراة، دورك أولاً.")
            await context.bot.send_message(row['challenger_id'], "🎯 دورك الآن: اختر تخمينك من 1 إلى 150.", reply_markup=friend_guess_keyboard(challenge_id, 0))
            await context.bot.send_message(row['opponent_id'], "⚔️ بدأت المباراة! انتظر دور اللاعب الآخر.")
        except Exception:
            pass
        await q.edit_message_text("قبلت التحدي! تم بدء المباراة، اللاعب الذي أرسل الدعوة يبدأ أولاً.", reply_markup=back())
        return

    if data.startswith("fcpage:"):
        parts = data.split(":")
        challenge_id, page = int(parts[1]), int(parts[2])
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM friend_challenges WHERE id=%s AND status='active'", (challenge_id,))
                game = cur.fetchone()
        if not game or game['current_turn'] != uid:
            await q.edit_message_text("هذا ليس دورك أو أن المباراة انتهت.", reply_markup=back())
            return
        await q.edit_message_text(f"🎯 دورك في التحدي #{challenge_id}. اختر رقماً من 1 إلى 150.", reply_markup=friend_guess_keyboard(challenge_id, page))
        return

    if data.startswith("fcg:"):
        _, challenge_raw, guess_raw = data.split(":")
        challenge_id, guess = int(challenge_raw), int(guess_raw)
        if not 1 <= guess <= 150:
            return
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM friend_challenges WHERE id=%s FOR UPDATE", (challenge_id,))
                game = cur.fetchone()
                if not game or game['status'] != 'active' or game['current_turn'] != uid:
                    await q.edit_message_text("هذا ليس دورك أو أن المباراة انتهت.", reply_markup=back())
                    return
                challenger = game['challenger_id']
                opponent = game['opponent_id']
                is_challenger = uid == challenger
                col = 'challenger_attempts' if is_challenger else 'opponent_attempts'
                attempts = game[col] + 1
                cur.execute(f"UPDATE friend_challenges SET {col}=%s WHERE id=%s", (attempts, challenge_id))
                if guess == game['secret_number']:
                    winner = uid
                    loser = opponent if uid == challenger else challenger
                    now = int(time.time())
                    cur.execute("UPDATE friend_challenges SET status='finished',finished_at=%s WHERE id=%s", (now, challenge_id))
                    cur.execute("UPDATE players SET points=points+10,xp=xp+100,wins=wins+1 WHERE telegram_id=%s", (winner,))
                    cur.execute("UPDATE players SET losses=losses+1 WHERE telegram_id=%s", (loser,))
                    cur.execute("""INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at)
                                   VALUES(%s,'win',%s,10,100,%s)""", (winner, attempts, now))
                    cur.execute("""INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at)
                                   VALUES(%s,'loss',%s,0,0,%s)""", (loser, game['opponent_attempts'] if is_challenger else game['challenger_attempts'], now))
                    result = ('win', winner, loser, attempts)
                elif attempts >= 6 and game['challenger_attempts'] + game['opponent_attempts'] + 1 >= 12:
                    now = int(time.time())
                    cur.execute("UPDATE friend_challenges SET status='draw',finished_at=%s WHERE id=%s", (now, challenge_id))
                    result = ('draw', None, None, attempts)
                else:
                    next_turn = opponent if uid == challenger else challenger
                    cur.execute("UPDATE friend_challenges SET current_turn=%s WHERE id=%s", (next_turn, challenge_id))
                    result = ('continue', next_turn, None, attempts)
        if result[0] == 'win':
            await q.edit_message_text("🏆 صحيح! فزت بالتحدي وربحت 10 نقاط و100 XP!", reply_markup=back())
            try:
                await context.bot.send_message(result[2], "انتهت المباراة. خصمك خمّن الرقم وفاز عليك.", reply_markup=back())
            except Exception:
                pass
            return
        if result[0] == 'draw':
            await q.edit_message_text("🤝 انتهت المباراة بالتعادل! لم يتم خصم أو إضافة نقاط.", reply_markup=back())
            other = game['opponent_id'] if uid == game['challenger_id'] else game['challenger_id']
            try:
                await context.bot.send_message(other, "🤝 انتهت مباراتكما بالتعادل!", reply_markup=back())
            except Exception:
                pass
            return
        next_turn = result[1]
        hint = "أكبر 🔼" if guess < game['secret_number'] else "أصغر 🔽"
        try:
            await context.bot.send_message(next_turn, f"دورك الآن في التحدي #{challenge_id}. تخمين الخصم: {hint}\nمحاولاتك: {game['challenger_attempts'] if next_turn == game['challenger_id'] else game['opponent_attempts']}/6", reply_markup=friend_guess_keyboard(challenge_id, 0))
            await context.bot.send_message(uid, f"تخمينك: {guess}. {hint}\nتم تحويل الدور إلى خصمك.")
        except Exception:
            pass
        await q.edit_message_text(f"تم تسجيل تخمينك: {guess}. {hint}\nبانتظار دور الخصم.", reply_markup=back())
        return

    if data.startswith("guesspage:"):
        await render_guess(q, context, int(data.split(":")[1]))
        return
    if data.startswith("guess:"):
        game = context.user_data.get("game")
        if not game:
            await q.edit_message_text("لا توجد جولة نشطة.", reply_markup=back())
            return
        guess = int(data.split(":")[1])
        game["attempt"] += 1
        n = game["attempt"]
        if guess == game["number"]:
            pts = [10, 8, 6, 4, 2, 1][n-1]
            xp = [100, 85, 70, 60, 50, 40][n-1]
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute(
                        "UPDATE players SET points=points+%s,xp=xp+%s,wins=wins+1 WHERE telegram_id=%s",
                        (pts, xp, uid)
                    )
                    cur.execute("""
                        INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at)
                        VALUES(%s,'win',%s,%s,%s,%s)
                    """, (uid, n, pts, xp, int(time.time())))
            context.user_data.pop("game", None)
            await q.edit_message_text(
                f"🎉 صحيح! ربحت {pts} نقطة و{xp} XP.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎮 جولة جديدة", callback_data="m:play")],
                    [InlineKeyboardButton("القائمة", callback_data="m:home")]
                ])
            )
            return
        if n >= 6:
            answer = game["number"]
            context.user_data.pop("game", None)
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE players SET losses=losses+1 WHERE telegram_id=%s", (uid,))
                    cur.execute("""
                        INSERT INTO match_history(player_id,result,attempts,points_earned,xp_earned,played_at)
                        VALUES(%s,'loss',%s,0,0,%s)
                    """, (uid, n, int(time.time())))
            await q.edit_message_text(f"انتهت المحاولات. الرقم كان {answer}.", reply_markup=back())
            return
        hint = "أكبر 🔼" if guess < game["number"] else "أصغر 🔽"
        await render_guess(q, context, game.get("page", 0), f"{hint} — باقي {6-n} محاولات.")
        return

    if data == "game:cancel":
        context.user_data.pop("game", None)
        await q.edit_message_text("تم إنهاء الجولة.", reply_markup=back())
        return

    if data == "team:create":
        names = ["Shadow Wolves", "Neon Kings", "Royal Titans", "Dark Phoenix",
                 "Cyber Knights", "Golden Hawks", "Night Raiders", "Purple Storm"]
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM team_members WHERE player_id=%s", (uid,))
                if cur.fetchone():
                    await q.edit_message_text("أنت منضم لفريق بالفعل.", reply_markup=back())
                    return
                chosen = None
                for name in names:
                    cur.execute("SELECT 1 FROM teams WHERE name=%s", (name,))
                    if not cur.fetchone():
                        chosen = name
                        break
                if not chosen:
                    chosen = "Shadow " + secrets.token_hex(2).upper()
                code = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8].upper()
                cur.execute("""
                    INSERT INTO teams(name,invite_code,leader_id,created_at)
                    VALUES(%s,%s,%s,%s) RETURNING id
                """, (chosen, code, uid, int(time.time())))
                tid = cur.fetchone()["id"]
                cur.execute("""
                    INSERT INTO team_members(team_id,player_id,role,joined_at)
                    VALUES(%s,%s,'leader',%s)
                """, (tid, uid, int(time.time())))
        await q.edit_message_text(f"تم إنشاء فريق {chosen} 👑\nرمز الدعوة: {code}", reply_markup=back())
        return

    if data == "team:list":
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM team_members WHERE player_id=%s", (uid,))
                member = cur.fetchone()
                cur.execute("""
                    SELECT t.id,t.name,COUNT(tm.player_id) AS n
                    FROM teams t LEFT JOIN team_members tm ON tm.team_id=t.id
                    GROUP BY t.id ORDER BY t.created_at DESC LIMIT 15
                """)
                teams = cur.fetchall()
        if member:
            await q.edit_message_text("أنت منضم لفريق بالفعل.", reply_markup=back())
            return
        buttons = [[InlineKeyboardButton(
            f"{t['name']} ({t['n']}/4)", callback_data=f"teamjoin:{t['id']}"
        )] for t in teams if t["n"] < 4]
        buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="m:teams")])
        await q.edit_message_text(
            "اختر فريقاً:" if len(buttons) > 1 else "لا توجد فرق متاحة.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("teamjoin:"):
        tid = int(data.split(":")[1])
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM team_members WHERE player_id=%s", (uid,))
                if cur.fetchone():
                    await q.edit_message_text("أنت منضم لفريق بالفعل.", reply_markup=back())
                    return
                cur.execute("SELECT name FROM teams WHERE id=%s", (tid,))
                team = cur.fetchone()
                cur.execute("SELECT COUNT(*) AS n FROM team_members WHERE team_id=%s", (tid,))
                count = cur.fetchone()["n"]
                if not team or count >= 4:
                    await q.edit_message_text("الفريق غير متاح أو مكتمل.", reply_markup=back())
                    return
                cur.execute("""
                    INSERT INTO team_members(team_id,player_id,role,joined_at)
                    VALUES(%s,%s,'member',%s)
                """, (tid, uid, int(time.time())))
        await q.edit_message_text(f"انضممت إلى {team['name']} ✅", reply_markup=back())
        return

    if data.startswith("tournament:"):
        tid = int(data.split(":")[1])
        with connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM tournaments WHERE id=%s AND status='open' FOR UPDATE", (tid,))
                t = cur.fetchone()
                if not t:
                    await q.edit_message_text("البطولة غير متاحة.", reply_markup=back())
                    return
                cur.execute("SELECT COUNT(*) AS n FROM tournament_entries WHERE tournament_id=%s", (tid,))
                count = cur.fetchone()["n"]
                cur.execute(
                    "SELECT 1 FROM tournament_entries WHERE tournament_id=%s AND player_id=%s",
                    (tid, uid)
                )
                j
