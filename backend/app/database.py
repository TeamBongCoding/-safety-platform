from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL


engine_options = {
    "pool_pre_ping": True,
}

database_backend = (
    DATABASE_URL.get_backend_name()
    if hasattr(DATABASE_URL, "get_backend_name")
    else DATABASE_URL.split(":", 1)[0].split("+", 1)[0]
)

if database_backend == "sqlite":
    engine_options["connect_args"] = {
        "check_same_thread": False,
    }
else:
    engine_options.update({
        "connect_args": {
            "sslmode": "require",
            "connect_timeout": 10,
        },
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle": 300,
    })

engine = create_engine(DATABASE_URL, **engine_options)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
