FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for cryptography and MySQL client bindings.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Export and install runtime Python dependencies from Poetry lockfile.
COPY pyproject.toml poetry.lock ./
RUN python -m pip install --no-cache-dir --upgrade pip poetry poetry-plugin-export && \
    poetry export --only main --format requirements.txt --without-hashes --output requirements.txt && \
    pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir boto3

# Copy application code
COPY syncbot/ ./syncbot/

WORKDIR /app/syncbot

EXPOSE 3000

CMD ["python", "app.py"]
