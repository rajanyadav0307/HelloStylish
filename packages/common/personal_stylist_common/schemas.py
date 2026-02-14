from pydantic import BaseModel, EmailStr


class CreateRunRequest(BaseModel):
    email: EmailStr
    trigger: str = "manual"


class RunEnvelope(BaseModel):
    run_id: str
