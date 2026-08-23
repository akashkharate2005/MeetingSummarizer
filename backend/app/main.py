from pathlib import Path
from datetime import datetime
import shutil
import urllib.parse
import urllib.request
import json
import logging
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import or_, desc
from sqlalchemy.orm import Session
from passlib.exc import UnknownHashError
from app.core.config import settings
from app.core.security import hash_password, verify_password, create_access_token
from app.db.database import Base, engine, get_db
from app.models.models import User, Meeting, AudioFile, Transcript, Summary, ActionItem
from app.schemas.schemas import *
from app.api.deps import get_current_user
from app.services.processor import process_meeting
from app.services.ai import _ffprobe_duration

logger = logging.getLogger("meeting_summarizer.main")

Base.metadata.create_all(bind=engine)
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)

app = FastAPI(title=settings.app_name, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ALLOWED = {"mp3","wav","m4a","flac","ogg","webm","mp4","mpeg","mpga"}


def _fetch_google_user_info(id_token: str | None = None, access_token: str | None = None) -> dict:
    if id_token:
        try:
            req = urllib.request.Request(f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(id_token)}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data.get("email"):
                    return {
                        "google_id": data.get("sub"),
                        "email": data.get("email"),
                        "name": data.get("name") or data.get("email").split("@")[0],
                        "picture": data.get("picture"),
                    }
        except Exception as e:
            logger.warning(f"Google tokeninfo check failed: {e}")

    if access_token:
        try:
            req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data.get("email"):
                    return {
                        "google_id": data.get("sub"),
                        "email": data.get("email"),
                        "name": data.get("name") or data.get("email").split("@")[0],
                        "picture": data.get("picture"),
                    }
        except Exception as e:
            logger.warning(f"Google userinfo check failed: {e}")

    raise HTTPException(401, "Invalid Google token or unable to retrieve Google user profile")


def _get_or_create_google_user(db: Session, user_info: dict) -> User:
    email = user_info["email"].lower().strip()
    google_id = user_info.get("google_id")
    name = user_info.get("name") or email.split("@")[0]
    avatar_url = user_info.get("picture")

    user = None
    if google_id:
        user = db.query(User).filter(User.google_id == google_id).first()

    if not user:
        user = db.query(User).filter(User.email.ilike(email)).first()

    if user:
        if not user.google_id and google_id:
            user.google_id = google_id
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        db.commit()
        db.refresh(user)
    else:
        user = User(
            name=name,
            email=email,
            password_hash=None,
            google_id=google_id,
            avatar_url=avatar_url,
            role="user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


@app.get("/api/health")
def health(): return {"status":"ok"}

@app.get("/api/auth/google/config", response_model=GoogleConfigOut)
def google_config():
    enabled = bool(settings.google_client_id and settings.google_client_id.strip())
    return GoogleConfigOut(enabled=enabled, client_id=settings.google_client_id if enabled else None)

@app.get("/api/auth/google/url")
def google_auth_url():
    if not settings.google_client_id:
        raise HTTPException(400, "Google OAuth is not configured on the server")
    params = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent select_account",
    })
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}

@app.get("/api/auth/google/callback")
def google_callback(code: str = Query(...), db: Session = Depends(get_db)):
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(400, "Google OAuth credentials not configured")

    token_url = "https://oauth2.googleapis.com/token"
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Google token exchange failed: {e}")
        raise HTTPException(400, "Failed to exchange authorization code with Google")

    id_token_str = token_data.get("id_token")
    access_token_google = token_data.get("access_token")

    user_info = _fetch_google_user_info(id_token=id_token_str, access_token=access_token_google)
    user = _get_or_create_google_user(db, user_info)
    app_jwt = create_access_token(user.id)

    redirect_target = f"{settings.frontend_url.rstrip('/')}/?token={urllib.parse.quote(app_jwt)}"
    return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)

@app.post("/api/auth/google", response_model=Token)
def google_authenticate(payload: GoogleAuthPayload, db: Session = Depends(get_db)):
    if not payload.credential and not payload.code:
        raise HTTPException(400, "Missing Google credential or authorization code")

    user_info = None
    if payload.credential:
        user_info = _fetch_google_user_info(id_token=payload.credential)
    elif payload.code:
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(400, "Google OAuth credentials not configured")
        token_url = "https://oauth2.googleapis.com/token"
        data = urllib.parse.urlencode({
            "code": payload.code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }).encode()
        req = urllib.request.Request(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode())
        user_info = _fetch_google_user_info(id_token=token_data.get("id_token"), access_token=token_data.get("access_token"))

    if not user_info or not user_info.get("email"):
        raise HTTPException(400, "Could not verify Google authentication")

    user = _get_or_create_google_user(db, user_info)
    return Token(access_token=create_access_token(user.id), user=user)

@app.post("/api/auth/register", response_model=Token)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first(): raise HTTPException(409, "Email already registered")
    user = User(name=payload.name, email=payload.email, password_hash=hash_password(payload.password))
    db.add(user); db.commit(); db.refresh(user)
    return Token(access_token=create_access_token(user.id), user=user)

@app.post("/api/auth/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return Token(access_token=create_access_token(user.id), user=user)

@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)): return user

@app.post("/api/meetings", response_model=MeetingOut)
async def upload_meeting(background: BackgroundTasks, file: UploadFile = File(...), title: str = Form("Untitled Meeting"), tags: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED: raise HTTPException(400, f"Unsupported audio format. Allowed: {', '.join(sorted(ALLOWED))}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    meeting = Meeting(user_id=user.id, title=title.strip() or "Untitled Meeting", meeting_date=datetime.utcnow(), tags=[x.strip() for x in tags.split(",") if x.strip()], status="queued")
    db.add(meeting); db.flush()
    path = Path(settings.storage_dir) / f"meeting_{meeting.id}.{ext}"

    # Stream the upload to disk instead of loading a potentially 500 MB file into RAM.
    size_bytes = 0
    try:
        with path.open("wb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB upload limit")
                destination.write(chunk)
    except HTTPException:
        if path.exists():
            path.unlink()
        db.rollback()
        raise
    except Exception:
        if path.exists():
            path.unlink()
        db.rollback()
        raise HTTPException(500, "Failed to store uploaded file")

    if size_bytes == 0:
        if path.exists():
            path.unlink()
        db.rollback()
        raise HTTPException(400, "Uploaded audio file is empty")

    dur_sec = int(round(_ffprobe_duration(str(path.resolve()))))
    if dur_sec > 0:
        meeting.duration_seconds = dur_sec

    db.add(AudioFile(meeting_id=meeting.id, storage_path=str(path.resolve()), original_name=file.filename or path.name, mime_type=file.content_type, format=ext, size_bytes=size_bytes))
    db.commit(); db.refresh(meeting)
    background.add_task(process_meeting, meeting.id)
    return meeting

@app.get("/api/meetings", response_model=list[MeetingListItem])
def list_meetings(q: str = Query(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Meeting).filter(Meeting.user_id == user.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Meeting.title.ilike(like)))
    return query.order_by(desc(Meeting.meeting_date)).all()

@app.get("/api/meetings/{meeting_id}", response_model=MeetingOut)
def get_meeting(meeting_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user.id).first()
    if not meeting: raise HTTPException(404, "Meeting not found")
    return meeting

@app.get("/api/meetings/{meeting_id}/audio")
def audio(meeting_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user.id).first()
    if not meeting or not meeting.audio: raise HTTPException(404, "Audio not found")
    if not Path(meeting.audio.storage_path).exists(): raise HTTPException(404, "Audio file not found on disk")
    return FileResponse(meeting.audio.storage_path, media_type=meeting.audio.mime_type or "audio/mpeg", filename=meeting.audio.original_name)

@app.patch("/api/action-items/{item_id}", response_model=ActionItemOut)
def update_action(item_id: int, payload: ActionItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).join(Summary).join(Meeting).filter(ActionItem.id == item_id, Meeting.user_id == user.id).first()
    if not item: raise HTTPException(404, "Action item not found")
    for field, value in payload.model_dump(exclude_unset=True).items(): setattr(item, field, value)
    if payload.completed is True: item.status = "completed"
    db.commit(); db.refresh(item); return item

@app.get("/api/meetings/{meeting_id}/export.txt", response_class=PlainTextResponse)
def export_text(meeting_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user.id).first()
    if not meeting: raise HTTPException(404, "Meeting not found")
    lines = [f"# {meeting.title}", "", f"Date: {meeting.meeting_date.isoformat()}", "", "## Summary", meeting.summary.summary_text if meeting.summary else "Not available", "", "## Decisions"]
    if meeting.summary:
        lines += [f"- {x}" for x in meeting.summary.decisions]
        lines += ["", "## Action Items"] + [f"- [{'x' if a.completed else ' '}] {a.description} | Owner: {a.owner or 'Unassigned'} | Due: {a.due_date or '—'}" for a in meeting.summary.action_items]
    lines += ["", "## Transcript"]

@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user.id).first()
    if not meeting: raise HTTPException(404, "Meeting not found")
    if meeting.audio and Path(meeting.audio.storage_path).exists(): Path(meeting.audio.storage_path).unlink()
    db.delete(meeting); db.commit(); return {"message":"deleted"}
