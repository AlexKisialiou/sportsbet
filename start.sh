#!/bin/bash
exec gunicorn wsgi:app
