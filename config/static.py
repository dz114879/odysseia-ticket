from pathlib import Path

APP_NAME = "Odysseia Ticket Bot"
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STORAGE_DIR = BASE_DIR / "storage"

SQLITE_FILENAME = "ticket_bot.sqlite3"
LOG_FILENAME = "ticket-bot.log"

STORAGE_SUBDIRECTORIES = (
    "snapshots",
    "notes",
    "archives",
    "exports",
)

DEFAULT_BOT_PREFIX = "!"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 30
DEFAULT_GUILD_TIMEZONE = "UTC"
