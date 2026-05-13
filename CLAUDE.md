# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Mamkiny Gamers United

Flask web app for UCL match score predictions among a small group of users. UI is in Russian.

## Stack
- Python / Flask, SQLAlchemy (PostgreSQL — locally and on Render)
- football-data.org API for Champions League and Premier League data
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
8. Calls `fetch_and_save_cl_matches()` — refreshes UCL data from API on every startup
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
| `Tour` | `name`, `season`, `round_number`, `league` (`"local"`/`"UCL"`/`"PL"`), `status` |
| `Match` | `tour_id`, `home_team_id`, `away_team_id`, `kickoff_time`, `status`, `featured` (bool) |
| `Score` | 1:1 with Match; `home_score`, `away_score` |
| `User` | `username`, `password_hash`, `is_admin`, `is_superuser`, `nickname`, `is_bot`, `avatar_emoji`, `avatar_color`, `superadmin_note`; `display_name` returns nickname or username |
| `Prediction` | `user_id`, `match_id`, `home_score`, `away_score`; unique on `(user_id, match_id)` |
| `PredictionPoints` | 1:1 with Prediction; `points` (0/1/3), `reason` (`"exact"`/`"winner"`/`"none"`) |
| `Commentary` | `match_label`, `text`; Bender's AI comments; `"__standings__"` label for leaderboard commentary |
| `Setting` | `key` (PK), `value`; stores `theme` setting |
| `ActivityLog` | `user_id` (FK nullable), `action`, `details`, `ip_address`, `created_at`; records all user/admin actions |

### Routes
| Blueprint | Route | Description |
|---|---|---|
| `main` | `GET /` | Main page (login required): leaderboard, predictions table, featured matches |
| `main` | `GET /admin` | Admin panel (admin required) |
| `main` | `GET /superadmin` | Superadmin panel: user management, activity log link, PL test matches |
| `main` | `GET /activity-log` | Activity log viewer (superuser only); filter by date range and user |
| `api` | `POST /api/cl-matches` | Fetch fresh UCL data from football-data.org |
| `api` | `POST /api/pl-matches` | Fetch EPL data (superuser only); optional `clear: true` to wipe existing PL data first |
| `api` | `POST /api/prediction` | Upsert user's prediction (login required) |
| `api` | `POST /api/featured-matches` | Set featured matches + regenerate Bender AI picks (admin) |
| `api` | `POST /api/simulate-results` | Mark matches finished (1:0), recalculate points, generate standings commentary (superuser) |
| `api` | `POST /api/settings/theme` | Set color theme: `navy`/`forest`/`purple`/`crimson` (superuser) |
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
- **🧪 Тест: матчи АПЛ** — load EPL matches with optional "clear existing PL data" checkbox
- **📋 Лог активности** — link to activity log page

### Activity Log (`/activity-log`)
- Table: timestamp, user (with superadmin_note), colored action badge, details, IP
- Filters: date-from, date-to, user dropdown; default last 7 days, limit 500

### Admin Panel (`/admin`)
- **Загрузить матчи ЛЧ** — fetches from football-data.org
- **Матчи для ставок** — checkbox list to mark featured matches (UCL + PL); triggers Bender AI pick generation
- **Симуляция результатов** — set selected matches to finished (1:0), recalculate points
- **Тема оформления** — 4 color themes: Синяя (navy), Зелёная (forest), Фиолет (purple), Алая (crimson)
- **Опасная зона** — reset scores only, or full DB reset (with confirmations)

### CSS Theming (`app/static/css/main.css`)
CSS custom properties on `:root` (navy default) + `[data-theme="forest|purple|crimson"]` overrides.  
Key variables: `--bg`, `--surface`, `--surface-hi`, `--surface-deep`, `--border`, `--border-sub`, `--accent`, `--accent-rgb`, `--accent-muted`, `--accent-bg`, `--accent-dim`, `--text`, `--text-muted`, `--text-dim`, `--text-label`.  
Applied via `data-theme` on `<body>` from `current_theme` template variable.  
Bender panel colors (gold/green) are hardcoded — not theme-dependent.

### Layout (`index.html`)
- Two-column grid: left 420px leaderboard + fill status, right: featured scheduled matches for betting
- Full-width predictions table below: finished matches from last 4 game days
- Floating bottom tray: 📊 Бендер об очках (gold chip), 📋 Прогноз (green chip)

### Services
- **`app/services/football_api.py`** — `fetch_and_save_cl_matches()`: UCL from `competitions/CL/matches`. `fetch_and_save_pl_matches()`: EPL from `competitions/PL/matches`, tours named "АПЛ Тур N" with `league="PL"`. Both upsert Teams/Tours/Matches/Scores and call `update_points_for_match()` on finished matches.
- **`app/services/points.py`** — `calc_points()`: 3 pts exact, 1 pt correct winner/draw, 0 otherwise. `update_points_for_match()`: upserts `PredictionPoints`. `get_leaderboard(last_days=N)`: users sorted by total with per-day breakdown.
- **`app/services/groq_api.py`** — `generate_bender_pick(home, away)` → analytical football forecast, parses `АНАЛИЗ:` / `СЧЁТ: X:Y`. `generate_bender_standings(text)` → Bender-persona leaderboard comment. Both use `llama-3.1-8b-instant`.
- **`app/services/activity.py`** — `log_action(user_id, action, details)`: writes to `ActivityLog`, captures IP from request context, never raises (own try/except). `ACTION_LABELS` dict maps action codes to Russian display names.

### Auth (`app/auth.py`)
`login_required`, `admin_required`, `superuser_required` decorators. `get_current_user()` reads `session["user_id"]`.

### Seed (`app/seed.py`)
Always creates the Bender bot user (`is_bot=True`). Creates up to 4 real users from env vars only if no non-bot users exist yet. USER2/USER3 are optional.

### Russian Team Names
`app/data/teams_ru.py` — `TEAMS_RU` dict mapping English → Russian names for ~50 clubs.

## Render Deployment
- **Build:** `pip install -r requirements.txt`
- **Start:** `bash start.sh` → `gunicorn wsgi:app`
- Set `DATABASE_URL`, `FOOTBALL_API_KEY`, `GROQ_API_KEY`, `SECRET_KEY`, `ADMIN_PASSWORD`, user credentials in Render env vars
- Render provides `postgres://` URL — auto-fixed to `postgresql://` in `create_app()`
- `RENDER` env var is set automatically by Render — enables secure cookies, HSTS, ProxyFix

## football-data.org API
- **Docs:** https://docs.football-data.org/general/v4/index.html
- Free tier: 10 req/min; Auth header: `X-Auth-Token: <key>`
- Endpoints: `GET /competitions/CL/matches` (UCL), `GET /competitions/PL/matches` (EPL)

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

### Match status mapping
| API value | Saved as |
|-----------|----------|
| `FINISHED` | `finished` |
| `IN_PLAY` / `PAUSED` | `live` |
| `SCHEDULED` / `TIMED` | `scheduled` |
