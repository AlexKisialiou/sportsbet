#!/bin/bash
python -c "from app import app, db, seed_data
with app.app_context():
    db.create_all()
    seed_data()
"
gunicorn app:app
