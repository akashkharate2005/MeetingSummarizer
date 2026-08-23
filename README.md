# MeetMind — AI-Powered Meeting Summarizer (Groq Free API)

A production-style MVP based on the supplied Meeting Summarizer SRS. It accepts meeting audio, stores it, transcribes it with Groq Whisper, generates structured meeting minutes with a Groq-hosted GPT-OSS model, and exposes the results through a React UI.

## Why Groq?

This version uses **GroqCloud** instead of Gemini/OpenAI. Groq provides a free API tier with model-specific rate limits, and its API supports both speech-to-text and structured JSON outputs. The free speech-to-text tier currently limits direct audio uploads to **25 MB**, so the backend automatically normalizes and splits larger recordings into small FLAC chunks before sending them to Whisper. Groq documents `whisper-large-v3-turbo` and `whisper-large-v3` for transcription and documents structured JSON schema output for GPT-OSS 20B/120B.

The application therefore keeps the website's **500 MB upload limit** while respecting Groq's smaller per-request audio limit.

## Stack

- Backend: Python + FastAPI + SQLAlchemy
- Database: PostgreSQL in Docker; SQLite can be used locally
- Storage: local object-storage-like directory for demo
- AI: GroqCloud API
- ASR: `whisper-large-v3-turbo`
- Summarization: `openai/gpt-oss-120b`
- Audio preprocessing: FFmpeg, 16 kHz mono FLAC chunks of about 5 minutes each
- Frontend: React + Vite
- Auth: JWT + bcrypt
- Deployment: Docker Compose

## Features

### Baseline
- Account registration/login
- Audio upload: MP3, WAV, M4A, FLAC, OGG, WebM and common MPEG variants
- **500 MB website upload limit**
- Large-file streaming to disk
- Automatic audio normalization/chunking for Groq's 25 MB free STT request limit
- Background processing with `queued → processing → summarizing → completed/failed`
- Transcript persistence
- Structured summary
- Key decisions
- Action items with owner/due date when available
- Action-item completion tracking
- Past-meeting list and title search
- Audio playback
- Transcript/summary export as text
- Meeting deletion

### QoL implemented
- Meeting tags field in the upload API
- Automatic status refresh while processing
- Responsive UI
- Strict structured AI output to reduce parsing failures
- Retry/backoff for transient Groq API errors
- Long-transcript map/reduce summarization

## 1. Get a free Groq API key

Create an API key in the GroqCloud console:

https://console.groq.com/keys

Put the key in `backend/.env`:

```env
GROQ_API_KEY=your_key_here
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
GROQ_FALLBACK_TRANSCRIPTION_MODEL=whisper-large-v3
GROQ_SUMMARY_MODEL=openai/gpt-oss-120b
```

Do **not** commit `backend/.env` to GitHub. The repository includes `.env.example` as a safe template.

## 2. Run with Docker Compose

```bash
docker compose up --build
```

Open:

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- Swagger: http://localhost:8000/docs

Create an account, upload an audio recording, and open the meeting to watch its status update automatically.

## 3. Run locally without Docker

### Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\\Scripts\\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux
```

You must also have **FFmpeg** installed and available on PATH because large audio files are normalized and chunked before transcription.

For a local SQLite demo, set:

```env
DATABASE_URL=sqlite:///./meeting_summarizer.db
STORAGE_DIR=./storage
CORS_ORIGINS=http://localhost:5173
```

Then:

```bash
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Environment variables

| Variable | Purpose | Example |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy connection | `postgresql+psycopg://meeting:meeting@db:5432/meeting_summarizer` |
| `SECRET_KEY` | JWT signing secret | long random string |
| `GROQ_API_KEY` | GroqCloud API key | `gsk_...` |
| `GROQ_TRANSCRIPTION_MODEL` | Primary Groq Whisper model | `whisper-large-v3-turbo` |
| `GROQ_FALLBACK_TRANSCRIPTION_MODEL` | Fallback Whisper model | `whisper-large-v3` |
| `GROQ_SUMMARY_MODEL` | Groq text model | `openai/gpt-oss-120b` |
| `MAX_UPLOAD_MB` | Website upload limit | `500` |
| `STORAGE_DIR` | Audio storage directory | `./storage` |
| `CORS_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |

## API overview

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/meetings`
- `GET /api/meetings?q=...`
- `GET /api/meetings/{id}`
- `GET /api/meetings/{id}/audio`
- `GET /api/meetings/{id}/export.txt`
- `PATCH /api/action-items/{id}`
- `DELETE /api/meetings/{id}`

## Architecture

```text
React/Vite
    |
    v
FastAPI REST API
    |--------- JWT authentication
    |
    +---- PostgreSQL / SQLite
    |
    +---- local object storage
    |
    +---- background processor
              |
              +---- FFmpeg normalization
              |          |
              |          v
              |     <= 5-minute FLAC chunks
              |          |
              |          v
              +---- Groq Whisper STT
              |          |
              |          v
              |       Transcript
              |
              +---- Groq GPT-OSS 120B
                         |
                         v
                   Structured JSON
                         |
                         +---- summary
                         +---- decisions
                         +---- action items
```

## Processing flow

1. User uploads meeting audio.
2. Backend streams the original file to local storage, so the 500 MB upload does not need to be loaded fully into RAM.
3. A background worker starts processing.
4. FFmpeg reads the audio and creates 16 kHz mono FLAC chunks of about 5 minutes each of at most 8 minutes each.
5. Each chunk is sent to Groq `whisper-large-v3-turbo` for transcription.
6. The chunk transcripts are combined in their original order.
7. The transcript is split into manageable text sections when needed.
8. Groq `openai/gpt-oss-120b` produces strict JSON containing the executive summary, decisions, and action items.
9. If multiple summary sections are needed, a final Groq call merges the partial summaries and removes duplicates.
10. PostgreSQL stores the transcript, summary, decisions, and action items.
11. The frontend polls the meeting status and displays the results.

## Large audio files and the free Groq tier

The website accepts up to **500 MB**, but that does not mean one 500 MB request is sent to Groq.

Groq's current speech-to-text documentation lists a **25 MB direct-upload limit on the free tier**. The backend therefore converts the recording to 16 kHz mono FLAC and splits it into 8-minute chunks. Eight minutes of 16 kHz mono 16-bit PCM is only about 15.4 MB before FLAC compression, so the resulting chunks stay comfortably below 25 MB.

Groq also publishes audio rate limits. The current free-plan limits shown for `whisper-large-v3-turbo` are 20 requests/minute, 2,000 requests/day, 7,200 audio seconds/hour and 28,800 audio seconds/day. A 36-minute meeting is therefore within the free audio-seconds allowance in a single hour, assuming the account has not already consumed that allowance.

A 500 MB recording can be accepted by the website, but very long recordings may exceed Groq's free audio-seconds quota. In that case the application will show the provider's error instead of pretending the meeting completed.

## Reliability

The Groq integration retries transient errors such as `429`, `500`, `502`, `503`, and `504` using exponential backoff. This helps when the provider is temporarily busy or the account briefly hits a rate limit.

## Evaluation mapping

The supplied SRS evaluates transcription accuracy, summary quality, prompt effectiveness and code structure.

For a project demonstration, show:
1. Registration/login
2. Audio upload
3. Queued/processing state
4. Transcript
5. Executive summary
6. Decisions
7. Action items and completion tracking
8. Audio playback
9. Export
10. Swagger API documentation

## Production hardening roadmap

For a true production deployment, replace the in-process FastAPI background task with Celery/RQ + Redis, replace local audio storage with S3-compatible object storage, add database migrations with Alembic, add rate limiting and audit logs, implement refresh tokens, add full-text/vector search for meeting Q&A, and add real-time WebSocket/SSE notifications.


## Reliability improvements for large recordings

The transcription pipeline is deliberately conservative for long recordings:

- Audio is split into **5-minute 16 kHz mono FLAC chunks**.
- Every chunk gets up to four attempts with exponential backoff for transient Groq errors.
- If one chunk repeatedly fails, that chunk is automatically split into two smaller pieces.
- The smaller pieces are retried with the primary Whisper model and then with `whisper-large-v3` as a fallback.
- A failure in one large chunk therefore does not immediately discard the whole meeting.
- The final transcript is assembled in the original order before summarization.

This is especially useful for recordings such as an 87 MB / ~36-minute meeting where one particular segment can occasionally trigger a provider-side 500 error.

## PostgreSQL startup reliability

Docker Compose includes a PostgreSQL health check and waits for the database to become healthy before starting the FastAPI backend. This prevents the common startup race where FastAPI attempts `Base.metadata.create_all()` before PostgreSQL is ready.
