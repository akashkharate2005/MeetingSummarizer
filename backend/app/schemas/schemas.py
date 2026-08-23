from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    avatar_url: str | None = None
    role: str
    model_config = ConfigDict(from_attributes=True)

class GoogleAuthPayload(BaseModel):
    credential: str | None = None
    code: str | None = None

class GoogleConfigOut(BaseModel):
    enabled: bool
    client_id: str | None = None

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

class ActionItemOut(BaseModel):
    id: int
    description: str
    owner: str | None = None
    due_date: str | None = None
    status: str
    completed: bool
    model_config = ConfigDict(from_attributes=True)

class ActionItemUpdate(BaseModel):
    owner: str | None = None
    due_date: str | None = None
    status: str | None = None
    completed: bool | None = None

class SummaryOut(BaseModel):
    id: int
    summary_text: str
    decisions: list[str]
    action_items: list[ActionItemOut]
    model_config = ConfigDict(from_attributes=True)

class TranscriptOut(BaseModel):
    id: int
    text: str
    language: str | None = None
    speaker_segments: list = []
    model_config = ConfigDict(from_attributes=True)

class MeetingListItem(BaseModel):
    id: int
    title: str
    meeting_date: datetime
    duration_seconds: int | None = None
    status: str
    error_message: str | None = None
    tags: list = []
    model_config = ConfigDict(from_attributes=True)

class MeetingOut(BaseModel):
    id: int
    title: str
    meeting_date: datetime
    duration_seconds: int | None = None
    tags: list = []
    status: str
    error_message: str | None = None
    transcript: TranscriptOut | None = None
    summary: SummaryOut | None = None
    model_config = ConfigDict(from_attributes=True)
