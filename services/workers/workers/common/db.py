import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://stylist:stylist@localhost:5432/stylist")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def exec_one(query: str, params: dict):
    with engine.begin() as conn:
        return conn.execute(text(query), params).mappings().first()


def exec_all(query: str, params: dict):
    with engine.begin() as conn:
        return list(conn.execute(text(query), params).mappings())


def exec_write(query: str, params: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(text(query), params).rowcount
