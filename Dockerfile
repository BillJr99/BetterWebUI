FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY verification.py .
COPY scheduler.py .
COPY services/ services/
COPY static/ static/
COPY skills/ skills/

# App data (config, conversations, workspaces, uploads …) — mount as a volume
RUN mkdir -p data/uploads data/checkpoints data/tasks

# Workspace — map a host folder here so the assistant reads/writes your files.
# Override with -e WORKSPACE_DIR=... to point elsewhere inside the container.
RUN mkdir -p /workspace
VOLUME /workspace

EXPOSE 8765

ENV PORT=8765
ENV WORKSPACE_DIR=/workspace

CMD ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
