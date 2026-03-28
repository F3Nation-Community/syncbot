FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for cryptography and MySQL client bindings.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies from pinned requirements.
COPY syncbot/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY syncbot/ ./syncbot/

WORKDIR /app/syncbot

# Cloud Run sets PORT (default 8080); local dev may use 3000.
EXPOSE 8080

CMD ["python", "app.py"]
