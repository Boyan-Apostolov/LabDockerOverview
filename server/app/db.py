import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session

from .models import Base

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DASHBOARD_DB_PATH", "/data/dashboard.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# timeout=30 gives SQLite's own lock wait (busy_timeout) more room before raising
# "database is locked", now that the gunicorn worker uses threads instead of one at a time
connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        # WAL lets readers (dashboard pages) proceed without waiting on a writer (agent
        # reports); NORMAL trades a little durability for far fewer fsync stalls. Some
        # bind-mount filesystems don't support WAL's shared-memory locking - fall back
        # quietly to the default journal mode rather than crashing the app over it.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:
            logger.warning("could not enable SQLite WAL mode, continuing with defaults: %s", e)
        finally:
            cursor.close()

SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if IS_SQLITE else None
    Base.metadata.create_all(bind=engine)
