"""Database models for the waitlist registration bot."""

import datetime
from sqlalchemy import Column, BigInteger, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

Base = declarative_base()


class WaitlistEntry(Base):
    """A waitlist registration with desired username and generated password."""

    __tablename__ = "waitlist_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    wanted_username = Column(String(64), nullable=False, unique=True)
    password = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<WaitlistEntry {self.wanted_username} by user {self.user_id}>"


class Admin(Base):
    """An administrator who can download the waitlist export."""

    __tablename__ = "admins"

    user_id = Column(BigInteger, primary_key=True)

    def __repr__(self):
        return f"<Admin {self.user_id}>"


def init_db(engine_url: str):
    """Initialize database engine, create tables, and return Session factory."""
    engine = create_engine(engine_url, connect_args={"timeout": 30} if "sqlite" in engine_url else {})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
