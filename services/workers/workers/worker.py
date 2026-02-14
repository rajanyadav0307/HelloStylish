import os

from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery("workers", broker=BROKER_URL, backend=RESULT_BACKEND)

from workers.executors.crewai_step_executor import execute_step_impl  # noqa: E402


@celery_app.task(name="workers.worker.execute_step")
def execute_step(step_id: str, run_id: str, step_key: str):
    return execute_step_impl(step_id=step_id, run_id=run_id, step_key=step_key)
