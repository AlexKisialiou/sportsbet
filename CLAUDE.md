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
- **Tour** — a round or stage (league="local" or "UCL")
- **Match** — a single game with kickoff_time, status (scheduled/live/finished), external_id from API
- **Score** — home_score / away_score, linked 1:1 to Match

## Main Page Logic
Shows last 30 UCL matches ordered by date descending, grouped by stage (1/4 final, etc.).
Matches are loaded via the Champions League page button which calls `POST /api/cl-matches`.
