#!/bin/bash
cd /home/site/wwwroot

gunicorn \
  --bind=0.0.0.0:8000 \
  --workers=2 \
  --timeout=600 \
  -k uvicorn.workers.UvicornWorker \
  backend.main:app
