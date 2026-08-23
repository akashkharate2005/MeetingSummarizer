from pathlib import Path
from datetime import datetime
import shutil
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
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

Base.metadata.create_all(bind=engine)
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)

app = FastAPI(title=settings.app_name, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ALLOWED = {"mp3","wav","m4a","flac","ogg","webm","mp4","mpeg","mpga"}

@app.get("/api/health")
def health(): return {"status":"ok"}

@app.post("/api/auth/register", response_model=Token)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first(): raise HTTPException(409, "Email already registered")
    user = User(name=payload.name, email=payload.email, password_hash=hash_password(payload.password))
    db.add(user); db.commit(); db.refresh(user)
    return Token(access_token=create_access_token(user.id), user=user)

@app.post("/api/auth/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash): raise HTTPException(401, "Invalid email or password")
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
    lines += ["", "## Transcript", meeting.transcript.text if meeting.transcript else "Not available"]
    return "\n".join(lines)

@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user.id).first()
    if not meeting: raise HTTPException(404, "Meeting not found")
    if meeting.audio and Path(meeting.audio.storage_path).exists(): Path(meeting.audio.storage_path).unlink()
    db.delete(meeting); db.commit(); return {"message":"deleted"}
