"""Unit tests for the Mac Studio local inference service.

The diarization merge is a pure function carrying the whole correctness
burden of speaker-labeled output, so it is tested exhaustively without
any ML stack. The FastAPI layer is tested with the engine mocked.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from thinktank.local_inference.engine import merge_diarization


class TestMergeDiarization:
    def test_two_speakers_interleaved(self):
        segments = [
            (0.0, 5.0, "Hello and welcome."),
            (5.5, 9.0, "Thanks for having me."),
            (9.5, 14.0, "Let's dive in."),
        ]
        turns = [
            (0.0, 5.2, "SPEAKER_00"),
            (5.2, 9.2, "SPEAKER_01"),
            (9.2, 14.5, "SPEAKER_00"),
        ]
        out = merge_diarization(segments, turns)
        assert out == ("Speaker A: Hello and welcome.\nSpeaker B: Thanks for having me.\nSpeaker A: Let's dive in.")

    def test_consecutive_same_speaker_coalesces(self):
        segments = [(0.0, 3.0, "First sentence."), (3.0, 6.0, "Second sentence.")]
        turns = [(0.0, 6.0, "SPEAKER_00")]
        assert merge_diarization(segments, turns) == "Speaker A: First sentence. Second sentence."

    def test_labels_renamed_by_first_appearance(self):
        """Raw pyannote labels map to A/B/C by appearance order, not by
        their numeric suffix."""
        segments = [(0.0, 2.0, "I speak first."), (2.0, 4.0, "I speak second.")]
        turns = [(0.0, 2.0, "SPEAKER_07"), (2.0, 4.0, "SPEAKER_02")]
        out = merge_diarization(segments, turns)
        assert out.startswith("Speaker A: I speak first.")
        assert "Speaker B: I speak second." in out

    def test_gap_segment_attaches_to_previous_speaker(self):
        """A segment with no overlapping turn (diarizer missed a beat)
        continues the previous speaker instead of inventing one."""
        segments = [(0.0, 2.0, "Covered."), (10.0, 11.0, "Uncovered.")]
        turns = [(0.0, 2.0, "SPEAKER_00")]
        assert merge_diarization(segments, turns) == "Speaker A: Covered. Uncovered."

    def test_leading_gap_uses_unknown_bucket(self):
        """No previous speaker to attach to -> a stable fallback label."""
        segments = [(0.0, 1.0, "Orphan."), (5.0, 6.0, "Known.")]
        turns = [(5.0, 6.0, "SPEAKER_00")]
        out = merge_diarization(segments, turns)
        lines = out.split("\n")
        assert lines[0] == "Speaker A: Orphan."  # unknown bucket gets first label
        assert lines[1] == "Speaker B: Known."

    def test_best_overlap_wins(self):
        """A segment spanning two turns goes to the speaker covering more of it."""
        segments = [(0.0, 10.0, "Mostly the second voice.")]
        turns = [(0.0, 3.0, "SPEAKER_00"), (3.0, 10.0, "SPEAKER_01")]
        assert merge_diarization(segments, turns) == "Speaker A: Mostly the second voice."
        # SPEAKER_01 has 7s overlap vs 3s -- it should own the line, and as
        # the only label used it becomes Speaker A.

    def test_empty_segments(self):
        assert merge_diarization([], [(0.0, 1.0, "SPEAKER_00")]) == ""

    def test_blank_text_segments_dropped(self):
        segments = [(0.0, 1.0, "  "), (1.0, 2.0, "Real text.")]
        turns = [(0.0, 2.0, "SPEAKER_00")]
        assert merge_diarization(segments, turns) == "Speaker A: Real text."


class TestLocalInferenceApp:
    @pytest.fixture
    def client(self):
        with patch("thinktank.local_inference.engine.load_models"):
            from thinktank.local_inference.main import app

            with TestClient(app) as tc:
                yield tc

    def test_health_reports_backend(self, client):
        with patch("thinktank.local_inference.engine.models_loaded", return_value=True):
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["models_loaded"] is True
        assert body["backend"] == "parakeet-mlx+pyannote"

    def test_transcribe_uses_file_field(self, client):
        """The multipart field must be named 'file' -- gpu_client's request
        shape; a mismatch would 422 every worker request."""
        with patch(
            "thinktank.local_inference.main.engine.transcribe_diarized",
            return_value="Speaker A: hi",
        ):
            resp = client.post("/transcribe", files={"file": ("ep.wav", b"RIFF....", "audio/wav")})
        assert resp.status_code == 200
        assert resp.json() == {"text": "Speaker A: hi"}

    def test_transcribe_error_returns_500_json(self, client):
        with patch(
            "thinktank.local_inference.main.engine.transcribe_diarized",
            side_effect=RuntimeError("mlx exploded"),
        ):
            resp = client.post("/transcribe", files={"file": ("ep.wav", b"RIFF....", "audio/wav")})
        assert resp.status_code == 500
        assert "mlx exploded" in resp.json()["error"]


class TestGpuClientEnvOverrides:
    def test_env_int_garbage_falls_back(self):
        from thinktank.transcription.gpu_client import _env_int

        with patch.dict("os.environ", {"X_TEST_INT": "garbage"}):
            assert _env_int("X_TEST_INT", 42) == 42

    def test_env_int_override(self):
        from thinktank.transcription.gpu_client import _env_int

        with patch.dict("os.environ", {"X_TEST_INT": "36000"}):
            assert _env_int("X_TEST_INT", 42) == 36000
