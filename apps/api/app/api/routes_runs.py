from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.services.run_service import create_run, get_run

router = APIRouter(tags=["runs"])


class CreateRunReq(BaseModel):
    email: EmailStr
    trigger: str = "manual"


@router.post("/runs")
def post_runs(req: CreateRunReq):
    run_id = create_run(email=req.email, trigger=req.trigger)
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_runs(run_id: str):
    return get_run(run_id)
