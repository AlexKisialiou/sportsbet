# SportsBet

Flask web app for viewing sports match results and betting.

## Stack
- Python / Flask
- SQLAlchemy (SQLite locally, PostgreSQL on Render)
- football-data.org API for Champions League data
- Bootstrap 5 (dark theme)
- Deployed on https://dashboard.render.com/

## Project Structure
```
app/
├── __init__.py          # create_app() factory
├── models.py            # Tour, Match, Score models
├── routes/
│   ├── main.py          # GET / and GET /champions-league
│   └── api.py           # POST /api/cl-matches
├── services/
│   └── football_api.py  # football-data.org integration
└── templates/
    ├── base.html        # shared layout and CSS
    ├── index.html       # main page — last 30 CL matches
    └── cl.html          # load matches button
wsgi.py                  # gunicorn entry point
```

## Environment Variables
| Variable | Description |
|----------|-------------|
| `FOOTBALL_API_KEY` | API key from football-data.org (free tier) |
| `DATABASE_URL` | PostgreSQL URL on Render, omit for local SQLite |
| `RESET_DB` | Set to `1` to drop and recreate DB on startup (local dev only) |

## Local Development
```
.\run_local.bat
```
- Creates `.venv` on first run, installs deps
- Loads `.env` automatically via python-dotenv
- App runs on http://localhost:5000

## Render Deployment
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `bash start.sh` → runs `gunicorn wsgi:app`
- Set `FOOTBALL_API_KEY` in Render environment variables
- Do NOT set `RESET_DB` on Render

## Data Model
- **Team** — club with `external_id` (from API), `name` (EN), `name_ru` (RU), `short_name`, `crest` (logo URL)
- **Tour** — a round or stage (`league="local"` or `"UCL"`)
- **Match** — a game linking two Teams via `home_team_id`/`away_team_id`, has `external_id`, `kickoff_time`, `status`
- **Score** — `home_score`/`away_score`, linked 1:1 to Match

`Team.display_name` property returns `name_ru` if set, otherwise `name`.

## Russian Team Names
`app/data/teams_ru.py` — dictionary `TEAMS_RU = { "English name": "Русское название" }`.
Applied automatically when saving teams from the API. If `name_ru` is already set manually in DB it is not overwritten.
To add a new team: add a line to `TEAMS_RU` and reload matches.

## football-data.org API
- **Docs:** https://docs.football-data.org/general/v4/index.html
- **Free tier limits:** 10 requests/minute, access to major competitions
- **Auth:** HTTP header `X-Auth-Token: <key>`
- **Base URL:** `https://api.football-data.org/v4/`

### Endpoints used
| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/competitions/CL/matches` | All CL matches current season |

### Match status values from API
| API value | Saved as |
|-----------|----------|
| `FINISHED` | `finished` |
| `IN_PLAY` | `live` |
| `PAUSED` | `live` |
| `SCHEDULED` | `scheduled` |
| `TIMED` | `scheduled` |

### Stage → Tour mapping (`STAGE_MAP` in `football_api.py`)
| API stage | Tour name | round_number |
|-----------|-----------|--------------|
| `GROUP_STAGE` | ЛЧ Групповой этап - Тур N | N (1–8) |
| `LAST_16` | ЛЧ 1/8 финала | 100 |
| `QUARTER_FINALS` | ЛЧ 1/4 финала | 200 |
| `SEMI_FINALS` | ЛЧ 1/2 финала | 300 |
| `FINAL` | ЛЧ Финал | 400 |

## Main Page Logic
Shows last 30 UCL matches ordered by date descending, grouped by stage.
Matches are loaded via `/champions-league` page button → `POST /api/cl-matches`.
