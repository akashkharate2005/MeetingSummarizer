from pathlib import Path


def test_groq_pipeline_uses_small_chunks_and_fallback():
    source = Path("backend/app/services/ai.py").read_text()
    assert "TRANSCRIPT_CHUNK_SECONDS = 5 * 60" in source
    assert "_transcribe_chunk_with_recovery" in source
    assert "settings.groq_fallback_transcription_model" in source
    assert "_split_failed_chunk" in source
