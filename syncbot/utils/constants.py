import os

SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
SLACK_STATE_S3_BUCKET_NAME = "ENV_SLACK_STATE_S3_BUCKET_NAME"
SLACK_INSTALLATION_S3_BUCKET_NAME = "ENV_SLACK_INSTALLATION_S3_BUCKET_NAME"
SLACK_CLIENT_ID = "ENV_SLACK_CLIENT_ID"
SLACK_CLIENT_SECRET = "ENV_SLACK_CLIENT_SECRET"
SLACK_SCOPES = "ENV_SLACK_SCOPES"
PASSWORD_ENCRYPT_KEY = "PASSWORD_ENCRYPT_KEY"

DATABASE_HOST = "DATABASE_HOST"
ADMIN_DATABASE_USER = "ADMIN_DATABASE_USER"
ADMIN_DATABASE_PASSWORD = "ADMIN_DATABASE_PASSWORD"
ADMIN_DATABASE_SCHEMA = "ADMIN_DATABASE_SCHEMA"

LOCAL_DEVELOPMENT = os.environ.get(SLACK_BOT_TOKEN, "123") != "123"

SLACK_STATE_S3_BUCKET_NAME = "ENV_SLACK_STATE_S3_BUCKET_NAME"
SLACK_INSTALLATION_S3_BUCKET_NAME = "ENV_SLACK_INSTALLATION_S3_BUCKET_NAME"
SLACK_CLIENT_ID = "ENV_SLACK_CLIENT_ID"
SLACK_CLIENT_SECRET = "ENV_SLACK_CLIENT_SECRET"
SLACK_SCOPES = "ENV_SLACK_SCOPES"
SLACK_SIGNING_SECRET = "SLACK_SIGNING_SECRET"

WARNING_BLOCK = "WARNING_BLOCK"

MAX_HEIF_SIZE = 1000

AWS_ACCESS_KEY_ID = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY = "AWS_SECRET_ACCESS_KEY"
S3_IMAGE_BUCKET = "syncbot-images"
S3_IMAGE_URL = f"https://{S3_IMAGE_BUCKET}.s3.amazonaws.com/"
