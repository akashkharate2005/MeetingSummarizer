from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, JSON, Boolean
from sqlalchemy.orm import relationship
from app.db.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    google_id = Column(String(255), unique=True, index=True, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    role = Column(String(30), default="user", nullable=False)
    meetings = relationship("Meeting", back_populates="user", cascade="all, delete-orphan")

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    meeting_date = Column(DateTime, default=datetime.utcnow)
    duration_seconds = Column(Integer, nullable=True)
    tags = Column(JSON, default=list)
    status = Column(String(30), default="queued", index=True)
    error_message = Column(Text, nullable=True)
    user = relationship("User", back_populates="meetings")
    audio = relationship("AudioFile", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    transcript = relationship("Transcript", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    summary = relationship("Summary", back_populates="meeting", uselist=False, cascade="all, delete-orphan")

class AudioFile(Base):
    __tablename__ = "audio_files"
    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), unique=True, nullable=False)
    storage_path = Column(String(500), nullable=False)
    original_name = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=True)
    format = Column(String(20), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    meeting = relationship("Meeting", back_populates="audio")

class Transcript(Base):
    __tablename__ = "transcripts"
    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), unique=True, nullable=False)
    text = Column(Text, nullable=False)
    language = Column(String(20), nullable=True)
    speaker_segments = Column(JSON, default=list)
    meeting = relationship("Meeting", back_populates="transcript")

class Summary(Base):
    __tablename__ = "summaries"
    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), unique=True, nullable=False)
    summary_text = Column(Text, nullable=False)
    decisions = Column(JSON, default=list)
    meeting = relationship("Meeting", back_populates="summary")
    action_items = relationship("ActionItem", back_populates="summary", cascade="all, delete-orphan")

class ActionItem(Base):
    __tablename__ = "action_items"
    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("summaries.id"), nullable=False)
    description = Column(Text, nullable=False)
    owner = Column(String(120), nullable=True)
    due_date = Column(String(80), nullable=True)
    status = Column(String(30), default="open", nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    summary = relationship("Summary", back_populates="action_items")
