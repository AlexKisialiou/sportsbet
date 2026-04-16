@echo off
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate

pip install -q -r requirements.txt

echo Starting Flask...
python wsgi.py
