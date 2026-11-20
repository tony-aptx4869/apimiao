"""WSGI 入口，供 gunicorn / uvicorn 使用：gunicorn -c gunicorn.conf.py wsgi:app"""
from app import create_app

app = create_app()
