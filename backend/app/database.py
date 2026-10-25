import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(Path(__file__).resolve().parents[1] / "codegrading.db"),
    )
)
DATABASE_URL = URL.create("sqlite", database=str(DATABASE_PATH))

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
BACKEND_DIR = Path(__file__).resolve().parents[1]


def init_db():
    import app.models  # noqa: F401

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        version = (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if "alembic_version" in tables
            else None
        )
        if "assignments" in tables and version is None:
            command.stamp(config, "20261024_0001")
        command.upgrade(config, "head")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
