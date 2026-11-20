# GUESS ARENA — Stage 2 Foundation

## Render settings
Build command: `pip install -r requirements.txt`
Start command: `python app.py`

Keep existing `BOT_TOKEN` and `DATABASE_URL`.
Add `ADMIN_IDS` as a comma-separated list of numeric Telegram user IDs.
Use `/myid` in the bot to display your own Telegram ID.

## Implemented in this stage
- PostgreSQL schema for players, teams, team membership, tournaments, entries,
  shop items, inventory, seasons and season deductions.
- Main menu with player and admin sections.
- Admin-only commands for stats, points adjustment, ban/unban, tournament creation,
  season creation and season ending with one-time 20% player-point deduction.
- Four-player team capacity, team creation and invite-code joining.
- Tournament listing and player registration.
- Shop purchases and inventory listing.
- Guessing game, profile, leaderboard and daily XP reward.
- GET and HEAD health checks.

## Not yet complete
Friend requests and in-bot friend challenges, team-vs-team match engine and
tournament brackets/results, item activation and match effects, team seasonal
rewards and longest-win-streak award, advanced game mode, Telegram Stars,
moderation workflows, detailed audit logs and full automated tests are still
planned for subsequent stages. Do not treat this as the final complete release.
