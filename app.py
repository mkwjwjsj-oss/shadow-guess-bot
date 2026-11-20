import os
import time
import random
import logging
import secrets
from datetime import datetime, timezone

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
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

class HealthHandler:
    # Kept as a tiny WSGI-free HTTP server for Render health checks.
    pass

def start_health_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"GUESS ARENA OK")
        def do_HEAD(self):
            self.send_response(200); self.end_headers()
        def log_message(self, *_):
            pass

    port = int(os.getenv("PORT", "10000"))
    Thread(target=lambda: HTTPServer(("0.0.0.0", port), Handler).serve_forever(),
           daemon=True).start()

def connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS players(
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                last_daily BIGINT NOT NULL DEFAULT 0,
                is_banned BOOLEAN NOT NULL DEFAULT FALSE,
                created_at BIGINT NOT NULL
            )""") 
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS display_name TEXT")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS teams(
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                invite_code TEXT NOT NULL UNIQUE,
                leader_id BIGINT NOT NULL REFERENCES players(telegram_id),
                team_points INTEGER NOT NULL DEFAULT 0,
                season_points INTEGER NOT NULL DEFAULT 0,
                created_at BIGINT NOT NULL
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS team_members(
                team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
                player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at BIGINT NOT NULL,
                PRIMARY KEY(team_id, player_id),
                UNIQUE(player_id)
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tournaments(
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
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tournament_entries(
                tournament_id BIGINT REFERENCES tournaments(id) ON DELETE CASCADE,
                player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
                joined_at BIGINT NOT NULL,
                PRIMARY KEY(tournament_id, player_id)
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS shop_items(
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                duration_hours INTEGER,
                uses INTEGER,
                active BOOLEAN NOT NULL DEFAULT TRUE
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory(
                id BIGSERIAL PRIMARY KEY,
                player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                item_id BIGINT REFERENCES shop_items(id),
                remaining_uses INTEGER,
                expires_at BIGINT,
                purchased_at BIGINT NOT NULL
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS seasons(
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                starts_at BIGINT NOT NULL,
                ends_at BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )""")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS season_deductions(
                season_id BIGINT REFERENCES seasons(id) ON DELETE CASCADE,
                player_id BIGINT REFERENCES players(telegram_id) ON DELETE CASCADE,
                deducted_points INTEGER NOT NULL,
                PRIMARY KEY(season_id, player_id)
            )""")
            # Seed the agreed 75-point daily-duration items and one-use items.
            cur.execute("""
                INSERT INTO shop_items(code,title,price,duration_hours,uses)
                VALUES
                  ('POINTS_X2','مضاعفة النقاط',75,24,NULL),
                  ('XP_X2','مضاعفة XP',75,24,NULL),
                  ('POINTS_SHIELD','حماية النقاط',75,24,NULL),
                  ('ONE_USE_150','عنصر استخدام واحد',150,NULL,1),
                  ('ONE_USE_300','عنصر استخدام واحد مميز',300,NULL,1)
                ON CONFLICT(code) DO NOTHING
            """)

def ensure_player(user):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO players(telegram_id,username,display_name,created_at)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=EXCLUDED.username, display_name=EXCLUDED.display_name
            """, (user.id, user.username, user.full_name, int(time.time())))

def player(tg_id):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM players WHERE telegram_id=%s", (tg_id,))
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
    if p["is_banned"]:
        await update.message.reply_text("هذا الحساب موقوف حالياً.")
        return
    await update.message.reply_text(
        "🏟️ أهلاً بك في GUESS ARENA 👑\nCHALLENGE • GUESS • WIN\nاختر من القائمة:",
        reply_markup=menu(update.effective_user.id)
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"معرّف تيليجرام الخاص بك: {update.effective_user.id}")

async def profile(update, context):
    p = player(update.effective_user.id)
    level = max(1, p["xp"] // 500 + 1)
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT t.name,tm.role FROM team_members tm
                JOIN teams t ON t.id=tm.team_id WHERE tm.player_id=%s
            """, (update.effective_user.id,))
            team = cur.fetchone()
    await update.message.reply_text(
        f"👤 الملف الشخصي\nالاسم: {p['display_name'] or 'لاعب'}\n"
        f"النقاط: {p['points']}\nXP: {p['xp']}\nالمستوى: {level}\n"
        f"الانتصارات: {p['wins']}\nالخسائر: {p['losses']}\n"
        f"الفريق: {team['name'] if team else 'بدون فريق'}"
    )

async def leaderboard(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT display_name,username,points,wins FROM players WHERE is_banned=FALSE ORDER BY points DESC,wins DESC LIMIT 10")
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("لا يوجد لاعبون في التصنيف بعد."); return
    lines = ["📊 أفضل 10 لاعبين:"]
    for i, p in enumerate(rows, 1):
        lines.append(f"{i}. {p['display_name'] or p['username'] or 'لاعب'} — {p['points']} نقطة")
    await update.message.reply_text("\n".join(lines))

async def daily(update, context):
    now = int(time.time()); uid = update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT last_daily FROM players WHERE telegram_id=%s FOR UPDATE", (uid,))
            row = cur.fetchone()
            if now - row["last_daily"] < 86400:
                hours = (86400 - (now-row["last_daily"])) // 3600
                await update.message.reply_text(f"استلمت مكافأتك. ارجع بعد {hours} ساعة تقريباً."); return
            cur.execute("UPDATE players SET xp=xp+20,last_daily=%s WHERE telegram_id=%s", (now,uid))
    await update.message.reply_text("🎁 حصلت على 20 XP!")

async def play(update, context):
    context.user_data["game"] = {"number":random.randint(1,150),"attempt":0}
    await update.message.reply_text("🎮 الوضع العادي: خمن رقماً من 1 إلى 150. لديك 6 محاولات.")

async def team_menu(update, context):
    await update.message.reply_text(
        "👥 نظام الفرق\nلإنشاء فريق أرسل: فريق جديد اسم الفريق\n"
        "للانضمام إلى فريق أرسل: انضم رمز_الدعوة\n"
        "كل لاعب يمكنه الانضمام إلى فريق واحد."
    )

async def create_team(update, context, name):
    uid = update.effective_user.id
    name = name.strip()
    if len(name) < 3 or len(name) > 24:
        await update.message.reply_text("اسم الفريق يجب أن يكون بين 3 و24 حرفاً."); return
    code = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8].upper()
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM team_members WHERE player_id=%s",(uid,))
            if cur.fetchone():
                await update.message.reply_text("أنت منضم إلى فريق بالفعل."); return
            cur.execute("INSERT INTO teams(name,invite_code,leader_id,created_at) VALUES(%s,%s,%s,%s) RETURNING id",
                        (name,code,uid,int(time.time())))
            tid=cur.fetchone()["id"]
            cur.execute("INSERT INTO team_members(team_id,player_id,role,joined_at) VALUES(%s,%s,'leader',%s)",
                        (tid,uid,int(time.time())))
    await update.message.reply_text(f"تم إنشاء فريق {name} ✅\nرمز الدعوة: {code}\nاحتفظ بالرمز وشاركه مع أعضاء فريقك.")

async def join_team(update, context, code):
    uid=update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT 1 FROM team_members WHERE player_id=%s",(uid,))
            if cur.fetchone():
                await update.message.reply_text("أنت منضم إلى فريق بالفعل."); return
            cur.execute("SELECT * FROM teams WHERE invite_code=%s",(code.upper(),))
            team=cur.fetchone()
            if not team:
                await update.message.reply_text("رمز الفريق غير صحيح."); return
            cur.execute("SELECT COUNT(*) AS n FROM team_members WHERE team_id=%s",(team["id"],))
            if cur.fetchone()["n"] >= 4:
                await update.message.reply_text("الفريق مكتمل (4 لاعبين)."); return
            cur.execute("INSERT INTO team_members(team_id,player_id,role,joined_at) VALUES(%s,%s,'member',%s)",
                        (team["id"],uid,int(time.time())))
    await update.message.reply_text(f"انضممت إلى فريق {team['name']} ✅")

async def tournaments(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM tournaments WHERE status IN ('open','active') ORDER BY created_at DESC LIMIT 15")
            rows=cur.fetchall()
    if not rows:
        await update.message.reply_text("لا توجد بطولات مفتوحة حالياً."); return
    lines=["🏆 البطولات المتاحة:"]
    for t in rows:
        lines.append(f"#{t['id']} — {t['title']} | {t['mode']} | الجائزة: {t['prize_points']} نقطة | /join_tournament {t['id']}")
    await update.message.reply_text("\n".join(lines))

async def join_tournament(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /join_tournament رقم_البطولة"); return
    tid=int(context.args[0]); uid=update.effective_user.id
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM tournaments WHERE id=%s AND status='open' FOR UPDATE",(tid,))
            t=cur.fetchone()
            if not t:
                await update.message.reply_text("البطولة غير موجودة أو التسجيل مغلق."); return
            cur.execute("SELECT COUNT(*) AS n FROM tournament_entries WHERE tournament_id=%s",(tid,))
            if cur.fetchone()["n"] >= t["max_players"]:
                await update.message.reply_text("اكتمل عدد المشاركين."); return
            cur.execute("INSERT INTO tournament_entries(tournament_id,player_id,joined_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (tid,uid,int(time.time())))
    await update.message.reply_text(f"تم تسجيلك في بطولة {t['title']} ✅")

async def shop(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM shop_items WHERE active=TRUE ORDER BY price")
            rows=cur.fetchall()
    lines=["🛒 المتجر:"]
    for item in rows:
        lines.append(f"{item['id']}. {item['title']} — {item['price']} نقطة | /buy {item['id']}")
    await update.message.reply_text("\n".join(lines))

async def buy(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /buy رقم_العنصر"); return
    iid=int(context.args[0]); uid=update.effective_user.id; now=int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM shop_items WHERE id=%s AND active=TRUE",(iid,))
            item=cur.fetchone()
            if not item:
                await update.message.reply_text("العنصر غير موجود."); return
            cur.execute("SELECT points FROM players WHERE telegram_id=%s FOR UPDATE",(uid,))
            p=cur.fetchone()
            if p["points"] < item["price"]:
                await update.message.reply_text("نقاطك لا تكفي لشراء هذا العنصر."); return
            cur.execute("UPDATE players SET points=points-%s WHERE telegram_id=%s",(item["price"],uid))
            expires=now+item["duration_hours"]*3600 if item["duration_hours"] else None
            cur.execute("INSERT INTO inventory(player_id,item_id,remaining_uses,expires_at,purchased_at) VALUES(%s,%s,%s,%s,%s)",
                        (uid,iid,item["uses"],expires,now))
    await update.message.reply_text(f"تم شراء {item['title']} وإضافته إلى مخزونك ✅")

async def inventory(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT s.title,i.remaining_uses,i.expires_at FROM inventory i
                JOIN shop_items s ON s.id=i.item_id
                WHERE i.player_id=%s AND (i.remaining_uses IS NULL OR i.remaining_uses>0)
                  AND (i.expires_at IS NULL OR i.expires_at>%s)
                ORDER BY i.purchased_at DESC
            """,(update.effective_user.id,int(time.time())))
            rows=cur.fetchall()
    if not rows:
        await update.message.reply_text("مخزونك فارغ حالياً."); return
    lines=["🎒 مخزني:"]
    for r in rows:
        detail = f"متبقي {r['remaining_uses']} استخدام" if r["remaining_uses"] is not None else "فعال لمدة 24 ساعة بعد التفعيل (التفعيل قيد الإضافة)"
        lines.append(f"• {r['title']} — {detail}")
    await update.message.reply_text("\n".join(lines))

async def season(update, context):
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM seasons WHERE status='active' ORDER BY starts_at DESC LIMIT 1")
            s=cur.fetchone()
            cur.execute("SELECT name,season_points FROM teams ORDER BY season_points DESC LIMIT 10")
            teams=cur.fetchall()
    if not s:
        await update.message.reply_text("لا يوجد موسم نشط حالياً."); return
    lines=[f"📅 {s['title']}", "تصنيف الفرق:"]
    lines += [f"{i}. {t['name']} — {t['season_points']}" for i,t in enumerate(teams,1)]
    await update.message.reply_text("\n".join(lines))

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("هذا القسم مخصص للإدارة فقط."); return
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
    if not is_admin(update.effective_user.id): return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM players"); players=cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM teams"); teams=cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM tournaments"); tourneys=cur.fetchone()["n"]
    await update.message.reply_text(f"📊 الإحصائيات\nاللاعبون: {players}\nالفرق: {teams}\nالبطولات: {tourneys}")

async def admin_create_tournament(update, context):
    if not is_admin(update.effective_user.id): return
    raw=" ".join(context.args)
    parts=raw.split("|")
    if len(parts)<4:
        await update.message.reply_text("الصيغة: /admin_create_tournament عنوان|الوصف|الجائزة|الحد_الأقصى"); return
    title,desc=parts[0].strip(),parts[1].strip()
    try: prize,maxp=int(parts[2]),int(parts[3])
    except ValueError:
        await update.message.reply_text("الجائزة والحد الأقصى يجب أن يكونا أرقاماً."); return
    if not title or prize<0 or maxp<2:
        await update.message.reply_text("تحقق من العنوان والجائزة والحد الأقصى."); return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("INSERT INTO tournaments(title,description,prize_points,max_players,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                        (title,desc,prize,maxp,update.effective_user.id,int(time.time())))
            tid=cur.fetchone()["id"]
    await update.message.reply_text(f"تم إنشاء البطولة #{tid} وفتح التسجيل ✅")

async def admin_points(update, context):
    if not is_admin(update.effective_user.id): return
    if len(context.args)!=2:
        await update.message.reply_text("الصيغة: /admin_points Telegram_ID مقدار_النقاط"); return
    try: uid,amount=int(context.args[0]),int(context.args[1])
    except ValueError:
        await update.message.reply_text("تأكد من الأرقام."); return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("UPDATE players SET points=GREATEST(0,points+%s) WHERE telegram_id=%s RETURNING points",(amount,uid))
            row=cur.fetchone()
    await update.message.reply_text(f"تم التعديل. الرصيد الحالي: {row['points']}" if row else "اللاعب غير موجود.")

async def admin_ban_common(update, context, banned):
    if not is_admin(update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("اكتب Telegram_ID بعد الأمر."); return
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("UPDATE players SET is_banned=%s WHERE telegram_id=%s RETURNING telegram_id",(banned,int(context.args[0])))
            row=cur.fetchone()
    await update.message.reply_text("تم تحديث حالة اللاعب." if row else "اللاعب غير موجود.")

async def admin_ban(update, context): await admin_ban_common(update,context,True)
async def admin_unban(update, context): await admin_ban_common(update,context,False)

async def admin_season(update, context):
    if not is_admin(update.effective_user.id): return
    parts=" ".join(context.args).split("|")
    if len(parts)!=2:
        await update.message.reply_text("الصيغة: /admin_season عنوان|عدد_أيام"); return
    try: days=int(parts[1])
    except ValueError:
        await update.message.reply_text("عدد الأيام يجب أن يكون رقماً."); return
    now=int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("UPDATE seasons SET status='ended' WHERE status='active'")
            cur.execute("INSERT INTO seasons(title,starts_at,ends_at,status) VALUES(%s,%s,%s,'active')",
                        (parts[0].strip(),now,now+days*86400))
    await update.message.reply_text("تم إنشاء الموسم الجديد.")

async def admin_end_season(update, context):
    if not is_admin(update.effective_user.id): return
    now=int(time.time())
    with connect() as con:
        with con.cursor() as cur:
            cur.execute("SELECT id FROM seasons WHERE status='active' ORDER BY starts_at DESC LIMIT 1")
            s=cur.fetchone()
            if not s:
                await update.message.reply_text("لا يوجد موسم نشط."); return
            sid=s["id"]
            cur.execute("""
                INSERT INTO season_deductions(season_id,player_id,deducted_points)
                SELECT %s,telegram_id,FLOOR(points*0.20)::int FROM players
                ON CONFLICT(season_id,player_id) DO NOTHING
            """,(sid,))
            cur.execute("""
                UPDATE players p SET points=GREATEST(0,p.points-d.deducted_points)
                FROM season_deductions d
                WHERE d.season_id=%s AND d.player_id=p.telegram_id
            """,(sid,))
            cur.execute("UPDATE seasons SET status='ended',ends_at=%s WHERE id=%s",(now,sid))
    await update.message.reply_text("انتهى الموسم وتم تطبيق خصم 20% مرة واحدة على نقاط اللاعبين.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    uid=update.effective_user.id
    p=player(uid)
    if p and p["is_banned"]:
        await update.message.reply_text("هذا الحساب موقوف حالياً."); return
    text=update.message.text.strip()
    if text=="🎮 العب": await play(update,context); return
    if text=="👤 الملف الشخصي": await profile(update,context); return
    if text=="📊 التصنيف": await leaderboard(update,context); return
    if text=="🎁 الدخول اليومي": await daily(update,context); return
    if text=="👥 الفرق": await team_menu(update,context); return
    if text=="🏆 البطولات": await tournaments(update,context); return
    if text=="🛒 المتجر": await shop(update,context); return
    if text=="🎒 مخزني": await inventory(update,context); return
    if text=="📅 الموسم": await season(update,context); return
    if text=="🛠 لوحة الأدمن": await admin_panel(update,context); return
    if text=="👫 الأصدقاء":
        await update.message.reply_text("قسم الأصدقاء قيد التطوير، وسيتم ربط طلبات الصداقة والتحديات في المرحلة التالية."); return
    if text.startswith("فريق جديد "):
        await create_team(update,context,text[len("فريق جديد "):]); return
    if text.startswith("انضم "):
        await join_team(update,context,text[len("انضم "):].strip()); return
    game=context.user_data.get("game")
    if game:
        try: guess=int(text)
        except ValueError:
            await update.message.reply_text("أرسل رقماً من 1 إلى 150."); return
        if not 1<=guess<=150:
            await update.message.reply_text("الرقم يجب أن يكون بين 1 و150."); return
        game["attempt"]+=1; n=game["attempt"]
        if guess==game["number"]:
            pts=[10,8,6,4,2,1][n-1]; xp=[100,85,70,60,50,40][n-1]
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE players SET points=points+%s,xp=xp+%s,wins=wins+1 WHERE telegram_id=%s",(pts,xp,uid))
            context.user_data.pop("game",None)
            await update.message.reply_text(f"صحيح! ربحت {pts} نقطة و{xp} XP 🎉"); return
        if n>=6:
            answer=game["number"]; context.user_data.pop("game",None)
            with connect() as con:
                with con.cursor() as cur:
                    cur.execute("UPDATE players SET losses=losses+1 WHERE telegram_id=%s",(uid,))
            await update.message.reply_text(f"انتهت المحاولات. الرقم كان {answer}."); return
        await update.message.reply_text(("أكبر 🔼" if guess<game["number"] else "أصغر 🔽")+f"\nباقي {6-n} محاولات."); return
    await update.message.reply_text("اختر من القائمة أو اكتب /start.",reply_markup=menu(uid))

def main():
    init_db()
    start_health_server()
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("myid",myid))
    app.add_handler(CommandHandler("join_tournament",join_tournament))
    app.add_handler(CommandHandler("buy",buy))
    app.add_handler(CommandHandler("admin",admin_panel))
    app.add_handler(CommandHandler("admin_stats",admin_stats))
    app.add_handler(CommandHandler("admin_create_tournament",admin_create_tournament))
    app.add_handler(CommandHandler("admin_points",admin_points))
    app.add_handler(CommandHandler("admin_ban",admin_ban))
    app.add_handler(CommandHandler("admin_unban",admin_unban))
    app.add_handler(CommandHandler("admin_season",admin_season))
    app.add_handler(CommandHandler("admin_end_season",admin_end_season))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_text))
    log.info("GUESS ARENA Stage 2 started")
    app.run_polling()

if __name__=="__main__":
    main()
