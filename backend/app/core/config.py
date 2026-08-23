from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Meeting Summarizer"
    database_url: str = "sqlite:///./meeting_summarizer.db"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 1440
    groq_api_key: str = ""
    groq_transcription_model: str = "whisper-large-v3-turbo"
    groq_fallback_transcription_model: str = "whisper-large-v3"
    groq_summary_model: str = "openai/gpt-oss-120b"
    max_upload_mb: int = 500
    storage_dir: str = "./storage"
    transcript_chunk_seconds: int = 180
    cors_origins: str = "http://localhost:5173"
    summarization_prompt: str = """Summarize the meeting transcript into a concise, factual, action-oriented structure. Do not invent information. Extract key decisions and action items. For each action item, include an owner and due date only when explicitly stated or strongly inferable; otherwise use null."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
