import os
import time
from datetime import datetime, timezone

from celery import Celery
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://stylist:stylist@localhost:5432/stylist")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
POLL_INTERVAL_SECONDS = float(os.getenv("ORCHESTRATOR_POLL_INTERVAL_SECONDS", "2"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
celery_app = Celery("orchestrator", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)


def _update_run_status(run_id, status: str):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE runs SET status=:status, finished_at=:finished_at WHERE id=:run_id"),
            {
                "status": status,
                "finished_at": datetime.now(timezone.utc),
                "run_id": run_id,
            },
        )


def _queue_step(step_id, run_id, step_key):
    with engine.begin() as conn:
        updated = conn.execute(
            text(
                """
                UPDATE run_steps
                SET status='QUEUED', attempt=attempt+1
                WHERE id=:step_id AND status='PENDING'
                """
            ),
            {"step_id": step_id},
        ).rowcount

    if updated:
        celery_app.send_task(
            "workers.worker.execute_step",
            args=[str(step_id), str(run_id), step_key],
        )


def process_once() -> None:
    with engine.begin() as conn:
        run_ids = conn.execute(
            text("SELECT id FROM runs WHERE status='RUNNING' ORDER BY created_at ASC")
        ).scalars().all()

    for run_id in run_ids:
        with engine.begin() as conn:
            steps = conn.execute(
                text(
                    """
                    SELECT id, step_index, step_key, status
                    FROM run_steps
                    WHERE run_id=:run_id
                    ORDER BY step_index ASC
                    """
                ),
                {"run_id": run_id},
            ).mappings().all()

        if not steps:
            _update_run_status(run_id, "FAILED")
            continue

        statuses = [row["status"] for row in steps]

        if any(status == "FAILED" for status in statuses):
            _update_run_status(run_id, "FAILED")
            continue

        if all(status == "SUCCEEDED" for status in statuses):
            _update_run_status(run_id, "SUCCEEDED")
            continue

        ready_step = None
        for row in steps:
            if row["status"] == "PENDING":
                ready_step = row
                break
            if row["status"] in {"QUEUED", "RUNNING"}:
                ready_step = None
                break

        if ready_step:
            _queue_step(
                step_id=ready_step["id"],
                run_id=run_id,
                step_key=ready_step["step_key"],
            )


def main() -> None:
    run_once = os.getenv("ORCHESTRATOR_RUN_ONCE") == "1"

    while True:
        process_once()
        if run_once:
            return
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
