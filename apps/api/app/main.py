from fastapi import FastAPI

from app.api.routes_drive import router as drive_router
from app.api.routes_runs import router as runs_router

app = FastAPI(title="HelloStylish API")
app.include_router(runs_router, prefix="/api")
app.include_router(drive_router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}
