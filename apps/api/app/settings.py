import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://stylist:stylist@localhost:5432/stylist",
)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI",
    "http://localhost:8000/api/drive/oauth/callback",
)
GOOGLE_DRIVE_SCOPE = os.getenv(
    "GOOGLE_DRIVE_SCOPE",
    "https://www.googleapis.com/auth/drive.readonly",
)
