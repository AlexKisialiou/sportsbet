# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Mamkiny Gamers United

Flask web app for UCL, EPL and World Cup match score predictions among a small group of users. UI is in Russian.

## Stack
- Python / Flask, SQLAlchemy (PostgreSQL — locally and on Render)
- football-data.org API for Champions League, Premier League and World Cup data
- Groq API (`llama-3.1-8b-instant`) for AI commentary via Bender bot
- Bootstrap 5 dark theme (CDN) + custom CSS in `app/static/css/main.css`
- `flask-limiter` for rate limiting (in-memory, fixed-window)
- Deployed on https://dashboard.render.com/

## Local Development
```
.\run_local.bat
```
Creates `.venv` on first run, installs deps, loads `.env` via python-dotenv. Runs on http://localhost:5000.

Local PostgreSQL: `postgresql://postgres:admin@localhost:5432/sportsbet`  
Create DB first: `createdb sportsbet`  
Schema `bet` is created automatically on startup; all tables live there (not in `public`).

## Environment Variables
| Variable                              | Description |
|---------------------------------------|-------------|
| `DATABASE_URL`                        | PostgreSQL URL. `postgres://` is auto-converted to `postgresql://` (Render compat). Falls back to SQLite if unset. |
| `FOOTBALL_API_KEY`                    | football-data.org free tier key |
| `GROQ_API_KEY`                        | Groq API key for Bender AI commentary |
| `SECRET_KEY`                          | Flask session secret |
| `ADMIN_PASSWORD`                      | Superuser login password (checked directly, not hashed) |
| `ADMIN_USERNAME/PASSWORD/такNICKNAME` | Seeded admin user credentials |
| `USER1_*/USER2_*/USER3_*`             | Seeded regular user credentials (USERNAME/PASSWORD/NICKNAME) |
| `RENDER`                              | Set automatically by Render; enables production mode (HTTPS cookies, HSTS, ProxyFix) |

Do NOT set `RESET_DB` — removed. Reset is done via admin panel.

## Architecture

### App Factory (`app/__init__.py`)
`create_app()` runs in this order:
1. Configures Flask + SQLAlchemy; fixes `postgres://` → `postgresql://`
2. For PostgreSQL: sets `SQLALCHEMY_ENGINE_OPTIONS` with `connect_args: {options: "-csearch_path=sportsbet"}` to pin all connections to the `bet` schema
3. Applies `ProxyFix` (production only) for real client IPs behind Render's proxy
4. Calls `_init_schema(db, "sportsbet")` — creates schema if missing, moves any tables still in `public` to `sportsbet`
5. `db.create_all()` — creates missing tables directly in `sportsbet`
6. Applies inline column migrations via raw `ALTER TABLE` + `try/except` (no Alembic)
7. Calls `seed.run()` — ensures bot user and real users exist
8. Calls `fetch_and_save_cl_matches()`, `fetch_and_save_pl_matches()`, `fetch_and_save_wc_matches()` — refreshes UCL+PL+WC data on every startup; triggers `maybe_generate_standings()` for each league after fetch
9. Registers three blueprints: `main_bp`, `api_bp`, `auth_bp`
10. Context processor injects: `current_user`, `APP_NAME`, `APP_VERSION`, `current_theme`
11. `after_request` sets security headers; 429 handler returns JSON for `/api/*`, HTML otherwise

### PostgreSQL Schema (`_init_schema`)
- `DB_SCHEMA = "bet"` constant in `__init__.py`
- `_init_schema` creates the schema, then moves any tables still in `public` using `ALTER TABLE public."tbl" SET SCHEMA bet` with per-table `SAVEPOINT`/`ROLLBACK TO SAVEPOINT` for safety
- All `sa_inspect` column reflection uses `schema=DB_SCHEMA` (or `None` for SQLite)
- SQLite dev environment is unaffected — all schema logic is gated by `is_postgres`

### Security
- **Rate limiting** (`app/limiter.py`): `flask-limiter`, IP-based, in-memory, fixed-window. Default 300/min; login 15/min + 60/hr; prediction 60/min; admin ops 3–10/min
- **Security headers** (every response): `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`; HSTS production-only
- **Session cookies**: `HttpOnly`, `SameSite=Lax`; `Secure` flag only in production (`RENDER` env var set)
- **Superuser auth**: password checked directly against `ADMIN_PASSWORD` env var, not hashed

### Config (`app/config.py`)
Central constants: `APP_NAME`, `APP_VERSION`, `POINTS_EXACT/WINNER/NONE`, `AVATAR_EMOJIS`, `AVATAR_COLORS`.

### Data Models (`app/models.py`)
| Model | Key Fields / Notes |
|---|---|
| `Team` | `external_id`, `name`, `name_ru`, `short_name`, `crest`; `display_name` returns RU name when set |
| `Tour` | `name`, `season`, `round_number`, `league` (`"local"`/`"UCL"`/`"PL"`/`"WC"`), `status` |
| `Match` | `tour_id`, `home_team_id`, `away_team_id`, `kickoff_time`, `status`, `featured` (bool) |
| `Score` | 1:1 with Match; `home_score`, `away_score`, `manual_lock` (bool) — if True, API updates are skipped |
| `User` | `username`, `password_hash`, `is_admin`, `is_superuser`, `nickname`, `is_bot`, `avatar_emoji`, `avatar_color`, `superadmin_note`; `display_name` returns nickname or username |
| `Prediction` | `user_id`, `match_id`, `home_score`, `away_score`; unique on `(user_id, match_id)` |
| `PredictionPoints` | 1:1 with Prediction; `points` (0/1/3), `reason` (`"exact"`/`"winner"`/`"none"`/`"manual"`), `manual_lock` (bool) — if True, recalculation is skipped |
| `Commentary` | `match_label`, `text`; Bender's AI comments; `"__standings__"` label for leaderboard commentary |
| `Setting` | `key` (PK), `value`; stores `theme`, `betting_locked`, `standings_day_ucl/pl/wc`, `league_enabled_UCL/PL/WC`, `league_order`, `pred_days_UCL/PL/WC` |
| `ActivityLog` | `user_id` (FK nullable), `action`, `details`, `ip_address`, `created_at`; records all user/admin actions |

### Routes
| Blueprint | Route | Description |
|---|---|---|
| `main` | `GET /` | Main page (login required): leaderboard, predictions table, featured matches |
| `main` | `GET /admin` | Admin panel (admin required) |
| `main` | `GET /superadmin` | Superadmin panel: user management, activity log link, PL test matches |
| `main` | `GET /activity-log` | Activity log viewer (superuser only); filter by date range and user |
| `api` | `POST /api/cl-matches` | Fetch fresh UCL data from football-data.org |
| `api` | `POST /api/pl-matches` | Fetch EPL data (admin); optional `clear: true` to wipe existing PL data first |
| `api` | `POST /api/wc-matches` | Fetch World Cup 2026 data (admin); optional `clear: true` to wipe existing WC data first |
| `api` | `POST /api/prediction` | Upsert user's prediction (login required) |
| `api` | `POST /api/featured-matches` | Set featured matches + regenerate Bender AI picks (admin) |
| `api` | `POST /api/simulate-results` | Mark matches finished (1:0), recalculate points, generate standings commentary (superuser) |
| `api` | `POST /api/settings/theme` | Set color theme: `purple` only (superuser) |
| `api` | `POST /api/settings/betting-lock` | Lock/unlock betting globally; body `{"locked": true/false}` (superuser) |
| `api` | `POST /api/settings/league-enabled` | Enable/disable a league; body `{"league":"UCL","enabled":false}` (superuser) |
| `api` | `POST /api/settings/league-order` | Set display order; body `{"order":["WC","UCL","PL"]}` (superuser) |
| `api` | `POST /api/settings/pred-days` | Set predictions table depth per league; body `{"league":"UCL","days":4}` (superuser) |
| `api` | `POST /api/admin/apply-teams-ru` | Apply TEAMS_RU dict to all teams in DB; returns updated count + missing list (superuser) |
| `api` | `POST /api/admin/translate-teams-ru` | Groq-translate teams with no name_ru (batches of 30, index-based parsing); returns updated + failed (superuser) |
| `api` | `POST /api/admin/match/<id>/score` | Manually set match score; sets status=finished, `Score.manual_lock=True`, auto-locks all prediction points (superuser) |
| `api` | `POST /api/admin/match/<id>/score/clear` | Remove manual score; deletes Score row, status→scheduled, API resumes auto-update (superuser) |
| `api` | `GET /api/admin/match/<id>/predictions` | Get all predictions + points for a match (superuser) |
| `api` | `POST /api/admin/prediction/<id>/points` | Manually set points (0/1/3) for a prediction; sets `manual_lock=True` (superuser) |
| `api` | `POST /api/admin/prediction/<id>/points/lock` | Lock existing prediction points without changing value (superuser) |
| `api` | `POST /api/admin/prediction/<id>/points/unlock` | Unlock prediction points; recalculates from current match score (superuser) |
| `api` | `POST /api/reset-scores` | Delete all Predictions + PredictionPoints (superuser, confirm=`"RESET"`) |
| `api` | `POST /api/reset-db` | Full DB drop+recreate+seed (superuser, confirm=`"RESET"`) |
| `api` | `POST /api/user/create` | Create new user (superuser only) |
| `api` | `POST /api/user/<id>/delete` | Delete user + cascade predictions/points (superuser; protects superusers, bots, self) |
| `api` | `POST /api/user/<id>/reset-password` | Reset password to username (superuser; cannot reset superuser) |
| `api` | `POST /api/user/<id>/set-admin` | Grant/revoke admin flag (superuser) |
| `api` | `POST /api/user/<id>/set-note` | Set private superadmin note on user (superuser) |
| `auth` | `GET/POST /login` | Session login; bot users blocked |
| `auth` | `GET /logout` | Clear session |
| `auth` | `GET/POST /profile` | Edit nickname, avatar emoji+color (login required) |

### Superadmin Panel (`/superadmin`)
- **User list**: all users with role icons, private note (shown in parentheses, superadmin-only), inline note editor, reset password, toggle admin, delete
- **Create user**: username, password, optional nickname
- **🏆 Управление лигами** — per-league: enable/disable toggle, ↑/↓ reorder buttons, days input (1–20, default 4) for predictions table depth; order stored in `league_order`, enabled in `league_enabled_UCL/PL/WC`, depth in `pred_days_UCL/PL/WC`
- **🌍 Русские названия команд** — «Применить из словаря» syncs `TEAMS_RU` to DB; «Перевести через Groq» sends untranslated teams to `llama-3.1-8b-instant` in batches of 30, parses response by index, saves `name_ru`
- **✏️ Результаты матчей** — per-league tabs showing all featured matches; inline score inputs with «Сохранить» (sets score + `manual_lock=True` on Score and all prediction points) / «Убрать» (deletes Score, status→scheduled, API resumes); «Ставки» expands per-match predictions table with per-user points selector (0/1/3), «Заблокировать» / «Разблокировать» per row; locked rows show 🔒 SA badge; route passes `edit_matches_by_league` dict
- **🔒 Приём ставок** — manual lock/unlock button; shows current state; calls `POST /api/settings/betting-lock`
- **📋 Лог активности** — link to activity log page

### Activity Log (`/activity-log`)
- Table: timestamp, user (with superadmin_note), colored action badge, details, IP
- Filters: date-from, date-to, user dropdown; default last 7 days, limit 500

### Admin Panel (`/admin`)
- **Загрузить матчи ЛЧ / АПЛ / ЧМ** — fetches from football-data.org (per-league tabs: UCL, PL, WC)
- **Матчи для ставок** — checkbox list to mark featured matches (UCL + PL + WC tabs); saves immediately, generates Bender AI picks in background (parallel Groq calls via ThreadPoolExecutor)
- **Опасная зона** — reset scores only, or full DB reset (with confirmations)
- Simulation UI removed (API endpoint `POST /api/simulate-results` kept)

### CSS Theming (`app/static/css/main.css`)
Only `purple` theme is active. CSS custom properties on `:root`.  
Key variables: `--bg`, `--surface`, `--surface-hi`, `--surface-deep`, `--border`, `--border-sub`, `--accent`, `--accent-rgb`, `--accent-muted`, `--accent-bg`, `--accent-dim`, `--text`, `--text-muted`, `--text-dim`, `--text-label`.  
Applied via `data-theme` on `<body>` from `current_theme` template variable.  
Bender panel colors (gold/green) are hardcoded — not theme-dependent.  
`.betting-bar` — per-league countdown strip; `.betting-bar.locked` — red locked state.

### Layout (`index.html`)
- League tabs rendered dynamically in `league_order` order; disabled leagues (`league_enabled_*=0`) hidden entirely
- Two-column grid per tab: left 420px leaderboard + fill status, right: featured scheduled matches for betting
- Per-league countdown bar (`.betting-bar`) above match list; auto-locks inputs at first kickoff time
- Inputs disabled when `betting_locked=True` (server) or tab locked by JS timer; 423 response also triggers lock
- Full-width predictions table below: finished matches from last N game days (configurable per league via `pred_days_*`, default 4)
- Floating bottom tray: 📊 Бендер об очках (gold chip per league), 📋 Прогноз (green chip per league); hidden for disabled leagues
- All times displayed in Europe/Minsk (UTC+3) via `| minsk` Jinja filter
- JS: `switchTab()` falls back to first enabled tab if saved sessionStorage tab is disabled; `lockBetting()` uses `querySelectorAll('.tab-btn')` dynamically

### Services
- **`app/services/football_api.py`** — `fetch_and_save_cl_matches()`: UCL from `competitions/CL/matches`. `fetch_and_save_pl_matches()`: EPL from `competitions/PL/matches`, tours named "АПЛ Тур N" with `league="PL"`. `fetch_and_save_wc_matches()`: World Cup from `competitions/WC/matches`, uses `WC_STAGE_MAP` with `league="WC"`. All three upsert Teams/Tours/Matches/Scores and call `update_points_for_match()` on finished matches. **Score update is skipped if `Score.manual_lock=True`.**
- **`app/services/points.py`** — `calc_points()`: 3 pts exact, 1 pt correct winner/draw, 0 otherwise. `update_points_for_match()`: upserts `PredictionPoints`; **skips predictions where `PredictionPoints.manual_lock=True`**. `get_leaderboard(last_days=N)`: users sorted by total with per-day breakdown.
- **`app/services/groq_api.py`** — `generate_bender_pick(home, away, competition)` → analytical football forecast, parses `АНАЛИЗ:` / `СЧЁТ: X:Y`; `competition` defaults to "Лига Чемпионов УЕФА". `generate_bender_standings(text)` → Bender-persona leaderboard comment. `translate_team_names(names)` → sends numbered list to `llama-3.1-8b-instant`, returns `{english: russian}` dict parsed by index (not by name, to avoid model renaming). `STANDINGS_LABEL_UCL/PL/WC` constants for Commentary labels. Bender picks use `llama-3.3-70b-versatile`; standings + translation use `llama-3.1-8b-instant`.
- **`app/services/activity.py`** — `log_action(user_id, action, details)`: writes to `ActivityLog`, captures IP from request context, never raises (own try/except). `ACTION_LABELS` dict maps action codes to Russian display names.
- **`app/services/standings.py`** — `maybe_generate_standings(league, app)`: called after every fetch (startup + API) for UCL, PL, WC. Runs in background thread. Groups featured matches by Minsk date; finds latest day where ALL are `finished`; checks `Setting[standings_day_{league}]` for idempotency; if new complete day found, generates Bender standings commentary via `generate_bender_standings()` and saves to `Commentary`.

### Auth (`app/auth.py`)
`login_required`, `admin_required`, `superuser_required` decorators. `get_current_user()` reads `session["user_id"]`.

### Timezone
All displayed times use Europe/Minsk (UTC+3, no DST). Implemented as `+timedelta(hours=3)` via `| minsk` Jinja2 filter registered in `create_app()`. Used in: index.html, admin.html, superadmin.html, activity_log.html.

### Seed (`app/seed.py`)
Always creates the Bender bot user (`is_bot=True`). Creates up to 4 real users from env vars only if no non-bot users exist yet. USER2/USER3 are optional.

### Russian Team Names
`app/data/teams_ru.py` — `TEAMS_RU` dict mapping English → Russian names for ~50 clubs. Applied to `team.name_ru` on team creation via `_get_or_create_team()`. Can be re-synced anytime via superadmin «Применить из словаря». Missing teams (not in dict, no name_ru) can be auto-translated via superadmin «Перевести через Groq» — only processes teams where `name_ru` is NULL or empty.

## Render Deployment
- **Build:** `pip install -r requirements.txt`
- **Start:** `bash start.sh` → `gunicorn wsgi:app`
- Set `DATABASE_URL`, `FOOTBALL_API_KEY`, `GROQ_API_KEY`, `SECRET_KEY`, `ADMIN_PASSWORD`, user credentials in Render env vars
- Render provides `postgres://` URL — auto-fixed to `postgresql://` in `create_app()`
- `RENDER` env var is set automatically by Render — enables secure cookies, HSTS, ProxyFix

## football-data.org API
- **Docs:** https://docs.football-data.org/general/v4/index.html
- Free tier: 10 req/min; Auth header: `X-Auth-Token: <key>`
- Endpoints: `GET /competitions/CL/matches` (UCL), `GET /competitions/PL/matches` (EPL), `GET /competitions/WC/matches` (World Cup)

### Stage → Tour mapping (UCL)
| API stage | Tour name | round_number |
|-----------|-----------|--------------|
| `GROUP_STAGE` | ЛЧ Групповой этап - Тур N | N (1–8) |
| `LAST_16` | ЛЧ 1/8 финала | 100 |
| `QUARTER_FINALS` | ЛЧ 1/4 финала | 200 |
| `SEMI_FINALS` | ЛЧ 1/2 финала | 300 |
| `FINAL` | ЛЧ Финал | 400 |

### Stage → Tour mapping (EPL)
| API field | Tour name | round_number |
|-----------|-----------|--------------|
| `matchday` N | АПЛ Тур N | N |

### Stage → Tour mapping (World Cup)
| API stage | Tour name | round_number |
|-----------|-----------|--------------|
| `GROUP_STAGE` | ЧМ Групповой этап - Тур N | N (1–3) |
| `ROUND_OF_16` | ЧМ 1/8 финала | 100 |
| `QUARTER_FINALS` | ЧМ 1/4 финала | 200 |
| `SEMI_FINALS` | ЧМ 1/2 финала | 300 |
| `THIRD_PLACE` | ЧМ За 3-е место | 350 |
| `FINAL` | ЧМ Финал | 400 |

### Match status mapping
| API value | Saved as |
|-----------|----------|
| `FINISHED` | `finished` |
| `IN_PLAY` / `PAUSED` | `live` |
| `SCHEDULED` / `TIMED` | `scheduled` |
