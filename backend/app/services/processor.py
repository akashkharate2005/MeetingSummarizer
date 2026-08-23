import logging
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models.models import Meeting, Transcript, Summary, ActionItem
from app.services.ai import transcribe_and_summarize

logger = logging.getLogger("meeting_summarizer.processor")


def process_meeting(meeting_id: int):
    db: Session = SessionLocal()
    try:
        meeting = db.get(Meeting, meeting_id)
        if not meeting or not meeting.audio:
            logger.warning(f"Meeting {meeting_id} not found or has no audio record")
            return

        meeting.status = "processing"
        meeting.error_message = None
        db.commit()

        logger.info(f"Starting processing for meeting {meeting_id} ({meeting.title})...")
        result = transcribe_and_summarize(
            meeting.audio.storage_path,
            meeting.audio.mime_type,
        )

        transcript_text = result["transcript"]
        logger.info(f"[DATABASE] Transcript characters before save: {len(transcript_text)}")

        if result.get("duration_seconds"):
            meeting.duration_seconds = result["duration_seconds"]

        # If previous transcript/summary exist (e.g. on re-processing), clean them up
        speaker_segments = result.get("speaker_segments", [])
        if meeting.transcript:
            meeting.transcript.text = transcript_text
            meeting.transcript.language = result.get("language")
            meeting.transcript.speaker_segments = speaker_segments
        else:
            db.add(
                Transcript(
                    meeting_id=meeting.id,
                    text=transcript_text,
                    language=result.get("language"),
                    speaker_segments=speaker_segments,
                )
            )

        if meeting.summary:
            db.delete(meeting.summary)
            db.flush()

        summary = Summary(
            meeting_id=meeting.id,
            summary_text=result["summary"],
            decisions=result.get("decisions", []),
        )
        db.add(summary)
        db.flush()

        for item in result.get("action_items", []):
            db.add(
                ActionItem(
                    summary_id=summary.id,
                    description=item.get("description", ""),
                    owner=item.get("owner"),
                    due_date=item.get("due_date"),
                    status=item.get("status") or "pending",
                    completed=False,
                )
            )

        warnings = result.get("warnings", [])
        if warnings:
            meeting.status = "completed_with_warnings"
            meeting.error_message = "; ".join(warnings)[:2000]
        else:
            meeting.status = "completed"
            meeting.error_message = None

        db.commit()
        db.refresh(meeting)

        retrieved_len = len(meeting.transcript.text) if meeting.transcript else 0
        logger.info(f"[DATABASE] Transcript characters after retrieval: {retrieved_len}")
        logger.info(f"[STATUS] Meeting {meeting_id} successfully marked as '{meeting.status}'")

    except Exception as exc:
        db.rollback()
        logger.exception(f"Processing failed for meeting {meeting_id}: {exc}")
        meeting = db.get(Meeting, meeting_id)
        if meeting:
            meeting.status = "failed"
            meeting.error_message = str(exc)[:2000]
            db.commit()
    finally:
        db.close()
