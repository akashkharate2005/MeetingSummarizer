import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from groq import Groq

from app.core.config import settings

logger = logging.getLogger("meeting_summarizer.ai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "key_decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                },
                "required": ["description", "owner", "due_date", "status"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["executive_summary", "key_decisions", "action_items"],
    "additionalProperties": False,
}

SPEAKER_SEGMENTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "speaker_segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "timestamp", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["speaker_segments"],
    "additionalProperties": False,
}

TRANSCRIPTION_RETRIES = 4
SUMMARY_RETRIES = 4
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_CHUNK_BYTES = 20 * 1024 * 1024  # 20 MB safety limit (Groq ceiling is 25 MB)


def client() -> Groq:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured in environment or .env")
    return Groq(api_key=settings.groq_api_key)


def _run_with_retry(fn, retries: int, operation: str):
    last_error = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            status = getattr(exc, "status_code", None)
            logger.warning(
                f"[RETRY] {operation} attempt {attempt + 1}/{retries} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if status is not None and status < 500 and status not in RETRYABLE_STATUS_CODES:
                if status in {400, 413}:
                    raise
                if attempt == 0:
                    raise
            if attempt == retries - 1:
                break
            delay = min(2 ** (attempt + 1), 20)
            time.sleep(delay)
    raise RuntimeError(
        f"Groq {operation} failed after {retries} attempts: {last_error}"
    ) from last_error


def _ffprobe_duration(path: str) -> float:
    ffprobe_bin = shutil.which("ffprobe") or ("/usr/bin/ffprobe" if os.path.exists("/usr/bin/ffprobe") else "ffprobe")
    ffmpeg_bin = shutil.which("ffmpeg") or ("/usr/bin/ffmpeg" if os.path.exists("/usr/bin/ffmpeg") else "ffmpeg")

    # 1. Try container format duration
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        val = result.stdout.strip()
        if val and val != "N/A" and float(val) > 0:
            return float(val)
    except Exception:
        pass

    # 2. Try audio stream duration
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        val = result.stdout.strip()
        if val and val != "N/A" and float(val) > 0:
            return float(val)
    except Exception:
        pass

    # 3. Fallback: fast stream decode check with ffmpeg
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-i", path, "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        for line in result.stderr.split("\n"):
            if "time=" in line:
                part = line.split("time=")[1].split()[0]
                h, m, s = part.split(":")
                val = float(h) * 3600 + float(m) * 60 + float(s)
                if val > 0:
                    return val
    except Exception as e:
        logger.warning(f"Duration check failed for {path}: {e}")

    return 0.0


def _make_chunks(path: str, target_chunk_seconds: int | None = None) -> tuple[list[dict], str | None]:
    """Create small 16 kHz mono FLAC chunks for Groq Whisper.

    Returns a list of dicts with chunk metadata:
    [{"path": ..., "index": 1, "start_sec": 0.0, "end_sec": 180.0, "duration": 180.0, "size_bytes": ...}, ...]
    and the temporary working directory.
    """
    chunk_sec = target_chunk_seconds or getattr(settings, "transcript_chunk_seconds", 180)
    total_duration = _ffprobe_duration(path)
    file_size = os.path.getsize(path) if os.path.exists(path) else 0

    logger.info(f"[AUDIO] Original file: {os.path.basename(path)}")
    logger.info(f"[AUDIO] Original size: {file_size} bytes ({file_size / (1024 * 1024):.2f} MB)")
    logger.info(f"[AUDIO] Original duration: {total_duration:.2f} seconds ({total_duration / 60:.2f} minutes)")

    if total_duration < 0.01 and file_size < 100:
        raise RuntimeError("Audio file is too short or empty. Minimum audio length is 0.01 seconds.")

    ffmpeg_bin = shutil.which("ffmpeg") or ("/usr/bin/ffmpeg" if os.path.exists("/usr/bin/ffmpeg") else "ffmpeg")
    work_dir = tempfile.mkdtemp(prefix="meeting-groq-")
    output_pattern = os.path.join(work_dir, "chunk_%04d.flac")

    subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "flac",
            "-compression_level",
            "5",
            "-f",
            "segment",
            "-segment_time",
            str(chunk_sec),
            "-reset_timestamps",
            "1",
            output_pattern,
        ],
        check=True,
    )

    raw_files = sorted(str(p) for p in Path(work_dir).glob("chunk_*.flac"))
    if not raw_files:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("No audio chunks could be generated from the file")

    chunks_meta = []
    current_start = 0.0

    for raw_path in raw_files:
        c_sz = os.path.getsize(raw_path) if os.path.exists(raw_path) else 0

        # Discard spurious empty 0-byte trailing artifacts created by segment muxer
        if c_sz < 1000 and len(raw_files) > 1 and raw_path == raw_files[-1]:
            if os.path.exists(raw_path):
                os.remove(raw_path)
            continue

        c_dur = _ffprobe_duration(raw_path)
        if c_dur <= 0.01:
            c_dur = min(float(chunk_sec), max(0.01, total_duration - current_start))

        # If a single chunk is still > MAX_CHUNK_BYTES (20 MB), split it in half
        if c_sz > MAX_CHUNK_BYTES and c_dur > 20:
            logger.warning(
                f"[CHUNKING] Chunk {raw_path} size ({c_sz / (1024*1024):.2f} MB) exceeds safety limit. Splitting..."
            )
            sub_files = _split_audio_file(raw_path, work_dir)
            if sub_files:
                for sub_path in sub_files:
                    sub_dur = _ffprobe_duration(sub_path) or (c_dur / 2.0)
                    sub_sz = os.path.getsize(sub_path)
                    sub_end = current_start + sub_dur
                    chunks_meta.append({
                        "path": sub_path,
                        "start_sec": current_start,
                        "end_sec": sub_end,
                        "duration": sub_dur,
                        "size_bytes": sub_sz,
                    })
                    current_start = sub_end
                if os.path.exists(raw_path):
                    os.remove(raw_path)
                continue

        c_end = current_start + c_dur
        chunks_meta.append({
            "path": raw_path,
            "start_sec": current_start,
            "end_sec": c_end,
            "duration": c_dur,
            "size_bytes": c_sz,
        })
        current_start = c_end

    # Assign sequential 1-based indices
    for idx, c in enumerate(chunks_meta, start=1):
        c["index"] = idx

    logger.info(f"[CHUNKING] Generated {len(chunks_meta)} chunks (target: {chunk_sec}s per chunk)")
    total_chunk_dur = 0.0
    for c in chunks_meta:
        total_chunk_dur += c["duration"]
        logger.info(
            f"  [CHUNKING] Chunk {c['index']}/{len(chunks_meta)}: "
            f"{c['start_sec']:.1f}s - {c['end_sec']:.1f}s | "
            f"size: {c['size_bytes'] / (1024*1024):.2f} MB | duration: {c['duration']:.2f}s"
        )
    logger.info(
        f"[CHUNKING] Total chunk duration sum: {total_chunk_dur:.2f}s "
        f"(diff from probed original: {abs(total_chunk_dur - total_duration):.2f}s)"
    )

    if not chunks_meta:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("No valid audio chunks could be generated from the file")

    return chunks_meta, work_dir


def _split_audio_file(chunk_path: str, work_dir: str) -> list[str]:
    """Split an audio file into two equal halves."""
    duration = _ffprobe_duration(chunk_path)
    if duration <= 10:
        return []

    half = duration / 2.0
    stem = Path(chunk_path).stem
    outputs = [
        os.path.join(work_dir, f"{stem}_part1.flac"),
        os.path.join(work_dir, f"{stem}_part2.flac"),
    ]

    ffmpeg_bin = shutil.which("ffmpeg") or ("/usr/bin/ffmpeg" if os.path.exists("/usr/bin/ffmpeg") else "ffmpeg")

    for start_sec, out_file in ((0.0, outputs[0]), (half, outputs[1])):
        subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_sec:.3f}",
                "-t",
                f"{half:.3f}",
                "-i",
                chunk_path,
                "-map",
                "0:a:0",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "flac",
                "-compression_level",
                "5",
                "-y",
                out_file,
            ],
            check=True,
        )

    valid_outputs = [p for p in outputs if os.path.exists(p) and os.path.getsize(p) > 0]
    return valid_outputs


def _transcribe_single_file(c: Groq, chunk_path: str, label: str, model: str) -> dict:
    """Send one file to Groq Whisper."""
    if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 100:
        return {"text": "", "language": None}

    def request():
        with open(chunk_path, "rb") as audio:
            return c.audio.transcriptions.create(
                file=(Path(chunk_path).name, audio.read()),
                model=model,
                response_format="verbose_json",
                temperature=0.0,
            )

    result = _run_with_retry(
        request,
        TRANSCRIPTION_RETRIES,
        f"transcription for {label} using {model}",
    )
    text = getattr(result, "text", "") or ""
    language = getattr(result, "language", None)
    return {"text": text.strip(), "language": language}


def _transcribe_chunk_with_recovery(
    c: Groq,
    chunk_meta: dict,
    total_chunks: int,
    work_dir: str,
) -> dict:
    """Transcribe a chunk with primary model, retry, sub-splitting, and fallback model."""
    index = chunk_meta["index"]
    chunk_path = chunk_meta["path"]
    primary = settings.groq_transcription_model
    fallback = getattr(settings, "groq_fallback_transcription_model", "whisper-large-v3")

    logger.info(f"[GROQ] Transcribing chunk {index}/{total_chunks} using {primary}...")

    try:
        res = _transcribe_single_file(c, chunk_path, f"chunk {index}/{total_chunks}", primary)
        logger.info(
            f"  [GROQ] Chunk {index}/{total_chunks} success: "
            f"{len(res['text'])} characters, {len(res['text'].split())} words"
        )
        return res
    except Exception as primary_error:
        logger.warning(
            f"[GROQ] Chunk {index}/{total_chunks} failed with {primary} ({primary_error}). Attempting split recovery..."
        )

        sub_files = _split_audio_file(chunk_path, work_dir)
        if not sub_files:
            if fallback and fallback != primary:
                logger.info(f"[GROQ] Retrying chunk {index}/{total_chunks} directly with fallback model {fallback}...")
                try:
                    res = _transcribe_single_file(c, chunk_path, f"chunk {index}/{total_chunks} fallback", fallback)
                    logger.info(
                        f"  [GROQ] Chunk {index}/{total_chunks} fallback success: "
                        f"{len(res['text'])} characters, {len(res['text'].split())} words"
                    )
                    return res
                except Exception as fb_err:
                    raise RuntimeError(
                        f"Chunk {index}/{total_chunks} failed with primary and fallback: {fb_err}"
                    ) from fb_err
            raise

        sub_texts = []
        languages = []

        for sub_idx, sub_path in enumerate(sub_files, start=1):
            sub_label = f"chunk {index}.{sub_idx}/{total_chunks}"
            sub_res = None
            try:
                sub_res = _transcribe_single_file(c, sub_path, sub_label, primary)
            except Exception as sub_err:
                if fallback and fallback != primary:
                    logger.info(f"[GROQ] Subchunk {sub_label} failed with primary ({sub_err}). Trying fallback {fallback}...")
                    try:
                        sub_res = _transcribe_single_file(c, sub_path, f"{sub_label} (fallback)", fallback)
                    except Exception as fb_err:
                        logger.warning(f"[GROQ] Subchunk {sub_label} failed with fallback: {fb_err}")
                else:
                    logger.warning(f"[GROQ] Subchunk {sub_label} failed: {sub_err}")

            if sub_res and sub_res.get("text"):
                sub_texts.append(sub_res["text"])
                if sub_res.get("language") and sub_res["language"] not in languages:
                    languages.append(sub_res["language"])

        combined_text = " ".join(t for t in sub_texts if t).strip()
        if combined_text:
            logger.info(
                f"  [GROQ] Chunk {index}/{total_chunks} recovered via splitting: "
                f"{len(combined_text)} characters, {len(combined_text.split())} words"
            )
            return {
                "text": combined_text,
                "language": languages[0] if languages else None,
            }

        # Fallback to entire chunk with fallback model if subchunks produced no text
        if fallback and fallback != primary:
            logger.info(f"[GROQ] Subchunks produced no text. Retrying whole chunk {index}/{total_chunks} with fallback model {fallback}...")
            return _transcribe_single_file(c, chunk_path, f"chunk {index}/{total_chunks} fallback", fallback)

        raise RuntimeError(f"Transcription for chunk {index}/{total_chunks} failed across all attempts")


def _summary_request(c: Groq, transcript_text: str, is_section: bool = False) -> dict:
    role_desc = (
        "You are an expert meeting analyst summarizing a section of a meeting transcript."
        if is_section
        else "You are an expert executive meeting-minutes assistant."
    )
    system = f"""{role_desc}
{settings.summarization_prompt}

Rules:
- Strictly use only facts, decisions, and commitments mentioned in the transcript text.
- Do not invent names, decisions, deadlines, owners, or action items.
- If an owner or due date is not stated or strongly inferable, use null.
- If there are no explicit decisions, return an empty array [] for key_decisions.
- If there are no explicit action items, return an empty array [] for action_items.
- Return structured JSON matching the supplied schema exactly.
"""

    def request():
        response = c.chat.completions.create(
            model=settings.groq_summary_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
            ],
            temperature=0.1,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "meeting_summary",
                    "strict": True,
                    "schema": SUMMARY_SCHEMA,
                },
            },
        )
        content = response.choices[0].message.content or ""
        if not content:
            raise RuntimeError("Groq returned an empty summary response")
        return json.loads(content)

    return _run_with_retry(request, SUMMARY_RETRIES, "summary generation")


def _chunk_text(text: str, max_chars: int = 10000) -> list[str]:
    """Split transcript text into balanced sections along paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        if current and (current_len + len(p_clean) + 2 > max_chars):
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(p_clean)
        current_len += len(p_clean) + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text]


def _merge_partial_summaries(c: Groq, partials: list[dict]) -> dict:
    if len(partials) == 1:
        return partials[0]

    packed = json.dumps(partials, ensure_ascii=False, indent=2)
    system = f"""You are the lead executive editor synthesising multiple section summaries of a long meeting.
{settings.summarization_prompt}

Guidelines:
- Consolidate all section executive summaries into a cohesive, high-level executive summary of the entire meeting.
- Combine and deduplicate all key decisions from all sections.
- Combine and deduplicate all action items from all sections.
- Do not invent facts or extrapolate beyond what is present in the input summaries.
- Return structured JSON matching the schema exactly.
"""

    def request():
        response = c.chat.completions.create(
            model=settings.groq_summary_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Section Summaries:\n\n{packed}"},
            ],
            temperature=0.1,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "meeting_summary",
                    "strict": True,
                    "schema": SUMMARY_SCHEMA,
                },
            },
        )
        content = response.choices[0].message.content or ""
        if not content:
            raise RuntimeError("Groq returned an empty merged summary")
        return json.loads(content)

    return _run_with_retry(request, SUMMARY_RETRIES, "hierarchical summary merge")


def _format_speaker_segments(c: Groq, transcript_text: str, total_duration_seconds: int = 0) -> list[dict]:
    """Segment and format transcript text into chronological speaker turns using Groq LLM."""
    if not transcript_text or not transcript_text.strip():
        return []

    sections = _chunk_text(transcript_text, max_chars=6000)
    all_segments = []
    sec_duration = float(total_duration_seconds) / max(1, len(sections)) if total_duration_seconds else 0.0

    logger.info(f"[SPEAKERS] Formatting speaker turns across {len(sections)} section(s)...")

    for s_idx, section in enumerate(sections, start=1):
        start_sec_offset = (s_idx - 1) * sec_duration
        start_m = int(start_sec_offset // 60)
        start_s = int(start_sec_offset % 60)
        offset_tag = f"{start_m:02d}:{start_s:02d}"

        system = f"""You are an expert conversational dialogue analyst.
Analyze the transcript section and segment it into sequential speaker turns with speaker labels and timestamps.

Guidelines:
- Detect natural turn-taking boundaries, dialogue shifts, questions, answers, and context changes.
- Assign clear speaker labels: e.g. "Speaker 1", "Speaker 2", or actual names if explicitly addressed/introduced (e.g. "Akash", "Host").
- If the section is a continuous monologue by one person, label all turns under that single speaker.
- Estimate start timestamps for each turn in MM:SS format, starting from offset {offset_tag}.
- Preserve the spoken text accurately without summarizing, altering, or omitting words.
- Return structured JSON matching the supplied schema exactly.
"""

        user_msg = f"Base Start Offset: {offset_tag}\n\nTranscript Section:\n\n{section}"

        def request():
            response = c.chat.completions.create(
                model=settings.groq_summary_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "speaker_segmentation",
                        "strict": True,
                        "schema": SPEAKER_SEGMENTATION_SCHEMA,
                    },
                },
            )
            content = response.choices[0].message.content or ""
            if not content:
                return {"speaker_segments": []}
            return json.loads(content)

        try:
            res = _run_with_retry(request, SUMMARY_RETRIES, f"speaker segmentation section {s_idx}/{len(sections)}")
            segs = res.get("speaker_segments", [])
            for seg in segs:
                if seg.get("text", "").strip():
                    all_segments.append({
                        "speaker": seg.get("speaker", "Speaker 1").strip(),
                        "timestamp": seg.get("timestamp", offset_tag).strip(),
                        "text": seg.get("text", "").strip(),
                    })
        except Exception as e:
            logger.warning(f"[SPEAKERS] Speaker segmentation failed for section {s_idx}: {e}")

    if not all_segments and transcript_text.strip():
        all_segments = [{
            "speaker": "Speaker 1",
            "timestamp": "00:00",
            "text": transcript_text.strip()
        }]

    return all_segments


def transcribe_and_summarize(path: str, mime_type: str | None = None) -> dict:
    """Execute complete end-to-end audio ingestion, chunking, STT, and summarization."""
    c = client()
    work_dir: str | None = None

    try:
        chunks_meta, work_dir = _make_chunks(path)
        total_chunks = len(chunks_meta)
        chunk_transcripts = []
        languages = []
        warnings = []

        for c_meta in chunks_meta:
            idx = c_meta["index"]
            try:
                res = _transcribe_chunk_with_recovery(c, c_meta, total_chunks, work_dir)
                chunk_transcripts.append(res["text"])
                if res.get("language") and res["language"] not in languages:
                    languages.append(res["language"])
            except Exception as chunk_exc:
                warning_msg = (
                    f"Chunk {idx}/{total_chunks} ({c_meta['start_sec']:.1f}s - {c_meta['end_sec']:.1f}s) "
                    f"failed: {chunk_exc}"
                )
                logger.error(f"[ERROR] {warning_msg}")
                warnings.append(warning_msg)
                placeholder = (
                    f"[Audio segment {idx}/{total_chunks} "
                    f"({c_meta['start_sec']:.1f}s - {c_meta['end_sec']:.1f}s) could not be transcribed]"
                )
                chunk_transcripts.append(placeholder)

        # Merge transcripts preserving chronological order
        full_transcript = "\n\n".join(t for t in chunk_transcripts if t.strip()).strip()

        successful_chunks = total_chunks - len(warnings)
        logger.info(
            f"[TRANSCRIPT] Total chunks: {successful_chunks}/{total_chunks} successful "
            f"(failed: {len(warnings)})"
        )
        logger.info(f"[TRANSCRIPT] Total characters: {len(full_transcript)}")
        logger.info(f"[TRANSCRIPT] Total words: {len(full_transcript.split())}")

        if not full_transcript or successful_chunks == 0:
            raise RuntimeError("No speech could be extracted from any audio segment")

        duration_sec = int(round(_ffprobe_duration(path)))

        # Speaker segmentation pass
        speaker_segments = _format_speaker_segments(c, full_transcript, duration_sec)
        logger.info(f"[SPEAKERS] Generated {len(speaker_segments)} speaker segment turns")

        # Hierarchical summarization
        text_sections = _chunk_text(full_transcript)
        logger.info(
            f"[SUMMARY] Input transcript characters: {len(full_transcript)} "
            f"(divided into {len(text_sections)} section(s))"
        )

        section_summaries = []
        for s_idx, section in enumerate(text_sections, start=1):
            logger.info(f"  [SUMMARY] Summarizing section {s_idx}/{len(text_sections)}...")
            summary_part = _summary_request(c, section, is_section=(len(text_sections) > 1))
            section_summaries.append(summary_part)

        if len(section_summaries) > 1:
            logger.info(f"[SUMMARY] Merging {len(section_summaries)} partial summaries...")
            final_summary = _merge_partial_summaries(c, section_summaries)
        else:
            final_summary = section_summaries[0]

        exec_summary = final_summary.get("executive_summary") or final_summary.get("summary", "")
        decisions = final_summary.get("key_decisions") or final_summary.get("decisions", [])
        action_items = final_summary.get("action_items", [])

        normalized_action_items = []
        for item in action_items:
            normalized_action_items.append({
                "description": item.get("description", ""),
                "owner": item.get("owner"),
                "due_date": item.get("due_date"),
                "status": item.get("status") or "pending",
            })

        logger.info(
            f"[SUMMARY] Final summary generated: {len(exec_summary.split())} words, "
            f"{len(decisions)} decisions, {len(normalized_action_items)} action items"
        )

        return {
            "transcript": full_transcript,
            "speaker_segments": speaker_segments,
            "language": languages[0] if languages else "en",
            "summary": exec_summary,
            "decisions": decisions,
            "action_items": normalized_action_items,
            "warnings": warnings,
            "duration_seconds": duration_sec,
        }
    finally:
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


def transcribe(path: str, mime_type: str | None = None) -> dict:
    result = transcribe_and_summarize(path, mime_type)
    return {
        "text": result["transcript"],
        "language": result.get("language"),
        "segments": [],
        "summary_result": result,
    }


def summarize(transcript: str) -> dict:
    c = client()
    text_sections = _chunk_text(transcript)
    section_summaries = [_summary_request(c, s, is_section=(len(text_sections) > 1)) for s in text_sections]
    final_summary = _merge_partial_summaries(c, section_summaries)
    return {
        "summary": final_summary.get("executive_summary") or final_summary.get("summary", ""),
        "decisions": final_summary.get("key_decisions") or final_summary.get("decisions", []),
        "action_items": final_summary.get("action_items", []),
    }
