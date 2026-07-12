import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from .models import Base

DB_PATH = os.environ.get("DASHBOARD_DB_PATH", "/data/dashboard.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if DATABASE_URL.startswith("sqlite") else None
    Base.metadata.create_all(bind=engine)
