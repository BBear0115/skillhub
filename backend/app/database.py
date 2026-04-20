from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from app.config import settings


def build_engine():
    if settings.database_url == "sqlite://":
        return create_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if settings.database_url.startswith("sqlite"):
        return create_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return create_engine(settings.database_url, echo=False)


engine = build_engine()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    with Session(engine) as session:
        yield session
