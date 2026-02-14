import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from sqlalchemy import text

from app.services.run_service import engine, ensure_user
from app.settings import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_DRIVE_SCOPE,
    GOOGLE_OAUTH_REDIRECT_URI,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API = "https://www.googleapis.com/drive/v3"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_google_oauth_config() -> None:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET. Configure them in environment before using Drive OAuth."
        )


def _get_user_id_by_email(email: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM users WHERE email=:email"), {"email": email}).mappings().first()
    return row["id"] if row else None


def _fetch_drive_connection(user_id):
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                SELECT user_id, access_token, refresh_token, token_expiry, scope, drive_user_email
                FROM drive_connections
                WHERE user_id=:user_id
                """
            ),
            {"user_id": user_id},
        ).mappings().first()


def _token_expiry(expires_in: int | str | None):
    if not expires_in:
        return None
    return _utcnow() + timedelta(seconds=max(0, int(expires_in) - 30))


def _upsert_drive_connection(
    user_id,
    access_token: str,
    refresh_token: str | None,
    token_expiry,
    scope: str | None,
    drive_user_email: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO drive_connections (
                  user_id, provider, access_token, refresh_token, token_expiry, scope, drive_user_email, created_at, updated_at
                )
                VALUES (
                  :user_id, 'google', :access_token, :refresh_token, :token_expiry, :scope, :drive_user_email, :now_ts, :now_ts
                )
                ON CONFLICT (user_id)
                DO UPDATE SET
                  access_token=EXCLUDED.access_token,
                  refresh_token=COALESCE(EXCLUDED.refresh_token, drive_connections.refresh_token),
                  token_expiry=EXCLUDED.token_expiry,
                  scope=EXCLUDED.scope,
                  drive_user_email=COALESCE(EXCLUDED.drive_user_email, drive_connections.drive_user_email),
                  updated_at=EXCLUDED.updated_at
                """
            ),
            {
                "user_id": user_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expiry": token_expiry,
                "scope": scope,
                "drive_user_email": drive_user_email,
                "now_ts": _utcnow(),
            },
        )


def _refresh_access_token(user_id, refresh_token: str):
    _require_google_oauth_config()

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Unable to refresh Drive access token: {response.text[:200]}")

    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Token refresh response missing access_token")

    _upsert_drive_connection(
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=_token_expiry(payload.get("expires_in")),
        scope=payload.get("scope", GOOGLE_DRIVE_SCOPE),
        drive_user_email=None,
    )
    return access_token


def _ensure_access_token(user_id):
    row = _fetch_drive_connection(user_id)
    if not row:
        raise RuntimeError("Drive not connected for this user. Complete OAuth first.")

    token_expiry = row.get("token_expiry")
    if token_expiry and token_expiry > _utcnow() + timedelta(seconds=60):
        return row["access_token"]

    if row.get("refresh_token"):
        return _refresh_access_token(user_id, row["refresh_token"])

    if row.get("access_token"):
        return row["access_token"]

    raise RuntimeError("Drive connection has no usable access token")


def _drive_get(access_token: str, path: str, params: dict | None = None) -> dict:
    response = requests.get(
        f"{GOOGLE_DRIVE_API}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=25,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Drive API request failed: {response.status_code} {response.text[:200]}")
    return response.json()


def create_oauth_start(email: str) -> dict:
    _require_google_oauth_config()

    user_id = ensure_user(email)
    state = secrets.token_urlsafe(24)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO drive_oauth_states (state, user_id, created_at) VALUES (:state, :user_id, :ts)"),
            {"state": state, "user_id": user_id, "ts": _utcnow()},
        )

    query = urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "login_hint": email,
            "state": state,
        }
    )

    return {
        "status": "ready",
        "auth_url": f"{GOOGLE_AUTH_URL}?{query}",
        "state": state,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
    }


def complete_oauth_callback(state: str, code: str) -> dict:
    _require_google_oauth_config()

    with engine.begin() as conn:
        state_row = conn.execute(
            text(
                """
                SELECT state, user_id, created_at
                FROM drive_oauth_states
                WHERE state=:state
                """
            ),
            {"state": state},
        ).mappings().first()

    if not state_row:
        raise ValueError("Invalid or expired OAuth state")

    state_created = state_row["created_at"]
    if state_created < _utcnow() - timedelta(minutes=20):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM drive_oauth_states WHERE state=:state"), {"state": state})
        raise ValueError("OAuth state expired. Start OAuth again.")

    token_response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if token_response.status_code >= 400:
        raise RuntimeError(f"OAuth token exchange failed: {token_response.text[:200]}")

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("OAuth token exchange did not return access_token")

    drive_user_email = None
    try:
        about = _drive_get(
            access_token,
            "/about",
            params={"fields": "user(emailAddress,displayName)"},
        )
        drive_user_email = about.get("user", {}).get("emailAddress")
    except Exception:
        drive_user_email = None

    _upsert_drive_connection(
        user_id=state_row["user_id"],
        access_token=access_token,
        refresh_token=token_data.get("refresh_token"),
        token_expiry=_token_expiry(token_data.get("expires_in")),
        scope=token_data.get("scope", GOOGLE_DRIVE_SCOPE),
        drive_user_email=drive_user_email,
    )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM drive_oauth_states WHERE state=:state"), {"state": state})

    return {
        "status": "connected",
        "drive_user_email": drive_user_email,
        "scope": token_data.get("scope", GOOGLE_DRIVE_SCOPE),
    }


def get_drive_status(email: str) -> dict:
    user_id = _get_user_id_by_email(email)
    if not user_id:
        return {"connected": False, "selected_folder": None}

    conn_row = _fetch_drive_connection(user_id)
    with engine.begin() as conn:
        folder = conn.execute(
            text(
                """
                SELECT folder_id, folder_name, created_at
                FROM drive_folders
                WHERE user_id=:user_id AND is_selected=TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).mappings().first()

    return {
        "connected": conn_row is not None,
        "drive_user_email": conn_row.get("drive_user_email") if conn_row else None,
        "selected_folder": dict(folder) if folder else None,
    }


def list_drive_folders(email: str) -> dict:
    user_id = _get_user_id_by_email(email)
    if not user_id:
        raise ValueError("Unknown user email")

    access_token = _ensure_access_token(user_id)
    payload = _drive_get(
        access_token,
        "/files",
        params={
            "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
            "fields": "files(id,name),nextPageToken",
            "pageSize": 200,
            "orderBy": "name_natural",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        },
    )

    folders = payload.get("files", [])
    return {"count": len(folders), "folders": folders}


def select_drive_folder(email: str, folder_id: str, folder_name: str | None) -> dict:
    user_id = _get_user_id_by_email(email)
    if not user_id:
        raise ValueError("Unknown user email")

    access_token = _ensure_access_token(user_id)

    resolved_name = folder_name
    if not resolved_name:
        metadata = _drive_get(
            access_token,
            f"/files/{folder_id}",
            params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
        )
        if metadata.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError("Provided file is not a Drive folder")
        resolved_name = metadata.get("name")

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE drive_folders SET is_selected=FALSE WHERE user_id=:user_id"),
            {"user_id": user_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO drive_folders (user_id, folder_id, folder_name, is_selected, created_at)
                VALUES (:user_id, :folder_id, :folder_name, TRUE, :created_at)
                ON CONFLICT (user_id, folder_id)
                DO UPDATE SET
                  folder_name=EXCLUDED.folder_name,
                  is_selected=TRUE
                """
            ),
            {
                "user_id": user_id,
                "folder_id": folder_id,
                "folder_name": resolved_name,
                "created_at": _utcnow(),
            },
        )

    return {
        "status": "selected",
        "folder_id": folder_id,
        "folder_name": resolved_name,
    }


def list_selected_folder_photos(email: str, limit: int = 30) -> dict:
    user_id = _get_user_id_by_email(email)
    if not user_id:
        raise ValueError("Unknown user email")

    with engine.begin() as conn:
        folder = conn.execute(
            text(
                """
                SELECT folder_id, folder_name
                FROM drive_folders
                WHERE user_id=:user_id AND is_selected=TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).mappings().first()

    if not folder:
        raise ValueError("No selected Drive folder. Call /api/drive/folder/select first.")

    access_token = _ensure_access_token(user_id)
    payload = _drive_get(
        access_token,
        "/files",
        params={
            "q": f"'{folder['folder_id']}' in parents and mimeType contains 'image/' and trashed=false",
            "fields": "files(id,name,mimeType,createdTime,webViewLink,thumbnailLink,imageMediaMetadata(width,height,time))",
            "orderBy": "createdTime desc",
            "pageSize": max(1, min(limit, 200)),
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        },
    )

    photos = payload.get("files", [])
    return {
        "folder": {"id": folder["folder_id"], "name": folder["folder_name"]},
        "count": len(photos),
        "photos": photos,
    }
