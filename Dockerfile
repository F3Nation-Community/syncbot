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
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir boto3

# Copy application code
COPY syncbot/ ./syncbot/

WORKDIR /app/syncbot

EXPOSE 3000

CMD ["python", "app.py"]
