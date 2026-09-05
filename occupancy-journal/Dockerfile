FROM python:3.13-alpine3.22
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY journal.py index.html ./
CMD ["python", "journal.py"]
