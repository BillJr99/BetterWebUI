FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ static/
COPY skills/ skills/

# Data directory is mounted as a volume at runtime so nothing is baked in
RUN mkdir -p data/uploads data/checkpoints data/tasks

EXPOSE 8765

ENV PORT=8765

CMD ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
