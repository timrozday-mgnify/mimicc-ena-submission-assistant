# MIMICC reads upload helper. Runs locally on the user's machine and spawns the
# enasequence/webin-cli sibling container on the host Docker daemon.
FROM python:3.12-slim

# Docker CLI so the helper can run the webin-cli sibling container via the
# mounted host Docker socket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/

ENV HELPER_PORT=9100
EXPOSE 9100
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${HELPER_PORT:-9100}"]
