from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr

from app.services.drive_service import (
    complete_oauth_callback,
    create_oauth_start,
    get_drive_status,
    list_drive_folders,
    list_selected_folder_photos,
    select_drive_folder,
)

router = APIRouter(tags=["drive"])


class OAuthStartReq(BaseModel):
    email: EmailStr


class FolderSelectReq(BaseModel):
    email: EmailStr
    folder_id: str
    folder_name: str | None = None


@router.post("/drive/oauth/start")
def oauth_start(req: OAuthStartReq):
    try:
        return create_oauth_start(email=req.email)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drive/oauth/callback")
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth provider returned error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code/state")
    try:
        return complete_oauth_callback(state=state, code=code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drive/status")
def drive_status(email: EmailStr = Query(...)):
    return get_drive_status(email=email)


@router.get("/drive/folders")
def drive_folders(email: EmailStr = Query(...)):
    try:
        return list_drive_folders(email=email)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drive/photos")
def drive_photos(email: EmailStr = Query(...), limit: int = Query(30, ge=1, le=200)):
    try:
        return list_selected_folder_photos(email=email, limit=limit)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drive/folder/select")
def folder_select(req: FolderSelectReq):
    try:
        return select_drive_folder(email=req.email, folder_id=req.folder_id, folder_name=req.folder_name)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
