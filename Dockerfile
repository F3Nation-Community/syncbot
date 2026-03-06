FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for cryptography and pillow-heif
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        default-libmysqlclient-dev \
        libheif-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY syncbot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir boto3

# Copy application code
COPY syncbot/ ./syncbot/

WORKDIR /app/syncbot

EXPOSE 3000

CMD ["python", "app.py"]
