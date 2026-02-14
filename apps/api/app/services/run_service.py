from collections.abc import Sequence
from sqlalchemy import create_engine, text

from app.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

LOCKED_STEPS: Sequence[tuple[str, str]] = [
    ("STYLE_BRIEF", "stylist"),
    ("DEALS", "a2"),
    ("BRAND_SEARCH", "a1"),
    ("RANK", "ranker"),
    ("TRYON", "tryon"),
    ("CHECKOUT_DRAFT", "checkout"),
]


def ensure_user(email: str):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO users (email)
                VALUES (:email)
                ON CONFLICT (email)
                DO UPDATE SET email = EXCLUDED.email
                RETURNING id
                """
            ),
            {"email": email},
        ).mappings().first()
    return row["id"]


def create_run(email: str, trigger: str = "manual") -> str:
    user_id = ensure_user(email)

    with engine.begin() as conn:
        run_row = conn.execute(
            text(
                """
                INSERT INTO runs (user_id, trigger, status)
                VALUES (:uid, :trigger, 'RUNNING')
                RETURNING id
                """
            ),
            {"uid": user_id, "trigger": trigger},
        ).mappings().first()
        run_id = run_row["id"]

        for idx, (step_key, agent_key) in enumerate(LOCKED_STEPS):
            conn.execute(
                text(
                    """
                    INSERT INTO run_steps (run_id, step_index, step_key, agent_key, status)
                    VALUES (:run_id, :step_index, :step_key, :agent_key, 'PENDING')
                    """
                ),
                {
                    "run_id": run_id,
                    "step_index": idx,
                    "step_key": step_key,
                    "agent_key": agent_key,
                },
            )

    return str(run_id)


def get_run(run_id: str) -> dict:
    with engine.begin() as conn:
        run = conn.execute(
            text(
                """
                SELECT id, user_id, trigger, status, created_at, finished_at
                FROM runs
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        ).mappings().first()

        steps = conn.execute(
            text(
                """
                SELECT step_index, step_key, agent_key, status, attempt, started_at, finished_at, error
                FROM run_steps
                WHERE run_id = :run_id
                ORDER BY step_index ASC
                """
            ),
            {"run_id": run_id},
        ).mappings().all()

        artifacts = conn.execute(
            text(
                """
                SELECT kind, inline_json, created_at
                FROM artifacts
                WHERE run_id = :run_id
                ORDER BY created_at ASC
                """
            ),
            {"run_id": run_id},
        ).mappings().all()

    return {
        "run": dict(run) if run else None,
        "steps": [dict(step) for step in steps],
        "artifacts": [dict(item) for item in artifacts],
    }
