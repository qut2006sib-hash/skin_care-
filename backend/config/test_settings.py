"""Fast diagnostics only. PostgreSQL concurrency acceptance uses config.settings."""
from .settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ.get("DIAGNOSTIC_DB", str(BASE_DIR / "diagnostic.sqlite3"))}}
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
