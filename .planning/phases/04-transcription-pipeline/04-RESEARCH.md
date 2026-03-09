# Phase 4: Transcription Pipeline - Research

**Researched:** 2026-03-08
**Domain:** Audio transcription, GPU inference, on-demand scaling, subprocess management
**Confidence:** HIGH

## Summary

Phase 4 implements the three-pass transcription pipeline that transforms content discovered in Phase 3 into full text transcripts stored in `content.body_text`. The pipeline is spec-defined (Section 7): Pass 1 extracts YouTube captions via yt-dlp (free, fast), Pass 2 checks for existing transcripts via per-source `transcript_url_pattern` config, and Pass 3 falls back to NVIDIA Parakeet TDT 1.1B GPU inference. The architecture splits across two services: the CPU worker runs the `process_content` handler orchestrating all three passes, and the GPU worker runs a dedicated service that loads the Parakeet model into VRAM once and serves transcription requests. The CPU worker also manages GPU scaling via Railway's GraphQL API.

The key complexity lies in: (1) the GPU worker service architecture -- loading a 1.1B parameter model and holding it across jobs, (2) subprocess management for yt-dlp audio download and ffmpeg audio conversion with temp file cleanup, (3) Railway API integration for on-demand GPU scaling, and (4) making the entire pipeline testable without a GPU or external services. All transcription-related error categories already exist in `ErrorCategory` (TRANSCRIPTION_FAILED, AUDIO_DOWNLOAD_FAILED, AUDIO_CONVERSION_FAILED), and `process_content` already has max_attempts=2 in the retry config.

**Primary recommendation:** Build the `process_content` handler on the CPU worker as a pure orchestrator that drives the three-pass logic, delegates GPU transcription to the GPU worker service via an internal HTTP endpoint (not direct NeMo calls), and uses `tempfile.NamedTemporaryFile` with context managers for audio lifecycle. The GPU worker is a separate FastAPI service (not a job queue consumer) that loads Parakeet once at startup and exposes a `/transcribe` endpoint.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TRANS-01 | Three-pass transcription pipeline: YouTube captions first, existing transcripts second, Parakeet GPU inference last | Three-pass orchestrator in `process_content` handler; yt-dlp Python API for captions, httpx for transcript fetch, GPU service for Parakeet |
| TRANS-02 | GPU Worker service running Parakeet TDT 1.1B on Railway L4, model persisted in VRAM across jobs | FastAPI GPU service with model loaded at startup via `nemo_asr.models.EncDecRNNTBPEModel.from_pretrained`; ~7GB VRAM on L4 (24GB available) |
| TRANS-03 | Audio download via yt-dlp (pinned to 2025.12.08) with ffmpeg conversion to 16kHz WAV | yt-dlp Python API with `FFmpegExtractAudio` postprocessor; ffmpeg subprocess for 16kHz mono conversion; tempfile management |
| TRANS-04 | On-demand GPU scaling via Railway API -- spin up when queue > threshold, shut down after idle timeout | Railway GraphQL API at `backboard.railway.com/graphql/v2` using `serviceInstanceUpdate` mutation; `manage_gpu_scaling` internal task |
| TRANS-05 | Audio temp file cleanup after transcription (audio never persisted to storage) | `tempfile.NamedTemporaryFile` with `finally` blocks; cleanup on both success and failure paths |
| TRANS-06 | Transcription output stored in `content.body_text` with metadata (word count, duration, source pass used) | Updates `content.body_text`, `content.word_count`, `content.transcription_method`, `content.status`, `content.processed_at` |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| yt-dlp | ==2025.12.08 | YouTube caption extraction + audio download | Spec-pinned version; Python API avoids subprocess overhead for captions; includes ffmpeg postprocessor integration |
| nemo_toolkit[asr] | >=2.0.0 | Parakeet TDT 1.1B model loading and inference | NVIDIA's official ASR framework; model available via `from_pretrained`; pre-installed in `nvcr.io/nvidia/nemo:24.05` base image |
| httpx | >=0.28.1 | Existing transcript fetch + GPU service communication | Already in project dependencies; async client for both external URLs and internal service calls |
| FastAPI | >=0.135.1 | GPU worker service HTTP interface | Already in project dependencies; lightweight service for model inference endpoint |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| soundfile | >=0.12.0 | Read WAV metadata (sample rate, duration) for validation | Verify ffmpeg conversion produced correct 16kHz mono WAV before sending to GPU |
| webvtt-py | >=0.5.1 | Parse WebVTT subtitle files from yt-dlp | Extract plain text from YouTube auto-generated captions (VTT format) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| yt-dlp for captions | youtube-transcript-api | Simpler API for captions only, but yt-dlp already needed for audio download so one less dependency |
| Internal HTTP for GPU | Job queue on GPU worker | HTTP is simpler for request/response pattern; GPU worker doesn't need queue complexity; CPU worker already manages scheduling |
| NeMo direct | Faster-Whisper | Whisper is easier to set up but spec explicitly requires Parakeet TDT 1.1B |

### Installation

```bash
# CPU worker additions (in pyproject.toml dependencies)
uv add "yt-dlp==2025.12.08" "webvtt-py>=0.5.1"

# GPU worker (nemo pre-installed in Docker base image nvcr.io/nvidia/nemo:24.05)
# soundfile also pre-installed in NeMo image
```

**Note:** ffmpeg is a system dependency, not a Python package. It must be available on `PATH` in the CPU worker Docker image. Add `RUN apt-get update && apt-get install -y ffmpeg` to `Dockerfile.worker-cpu`.

## Architecture Patterns

### Recommended Project Structure

```
src/thinktank/
├── transcription/           # NEW: Pure logic + subprocess wrappers
│   ├── __init__.py
│   ├── captions.py          # Pass 1: yt-dlp YouTube caption extraction
│   ├── existing.py          # Pass 2: Existing transcript fetch via httpx
│   ├── audio.py             # Audio download (yt-dlp) + ffmpeg conversion
│   └── gpu_client.py        # HTTP client for GPU worker service
├── handlers/
│   ├── process_content.py   # NEW: Three-pass orchestrator handler
│   └── registry.py          # Add process_content registration
├── gpu_worker/              # NEW: Separate GPU service
│   ├── __init__.py
│   ├── main.py              # FastAPI app with /transcribe and /health
│   └── model.py             # Parakeet model loader (singleton)
├── scaling/                 # NEW: Railway API integration
│   ├── __init__.py
│   └── railway.py           # GraphQL client for service scaling
└── worker/
    └── loop.py              # Add manage_gpu_scaling scheduler (like reclaim)
```

### Pattern 1: Three-Pass Orchestrator

**What:** The `process_content` handler implements a fallback chain. Each pass returns text or None. First non-None result wins.

**When to use:** Every content item with `status='pending'` and `content_type` in ('episode', 'video').

**Example:**

```python
async def handle_process_content(session: AsyncSession, job: Job) -> None:
    content_id = uuid.UUID(job.payload["content_id"])
    content = await session.get(Content, content_id)
    source = await session.get(Source, content.source_id)

    transcript: str | None = None
    method: str | None = None

    # Pass 1: YouTube captions (only for youtube_channel sources)
    if source.source_type == "youtube_channel":
        transcript = extract_youtube_captions(content.url)
        if transcript and len(transcript.split()) >= 100:
            method = "youtube_captions"

    # Pass 2: Existing transcript (if source has transcript_url_pattern)
    if transcript is None:
        pattern = source.config.get("transcript_url_pattern")
        if pattern:
            transcript = await fetch_existing_transcript(content.url, pattern)
            if transcript:
                method = "existing_transcript"

    # Pass 3: Parakeet GPU inference
    if transcript is None:
        transcript = await transcribe_via_gpu(content.url, content.duration_seconds)
        if transcript:
            method = "parakeet"

    if transcript is None:
        raise RuntimeError(f"All transcription passes failed for content {content_id}")

    # Update content
    content.body_text = transcript
    content.word_count = len(transcript.split())
    content.transcription_method = method
    content.status = "done"
    content.processed_at = _now()
    await session.commit()
```

### Pattern 2: GPU Worker as HTTP Service

**What:** The GPU worker loads Parakeet at startup and exposes a `/transcribe` endpoint. The CPU worker sends audio bytes via multipart POST.

**When to use:** All Pass 3 (Parakeet) transcriptions.

**Example:**

```python
# gpu_worker/model.py -- Singleton model loader
_model = None

def get_model():
    global _model
    if _model is None:
        import nemo.collections.asr as nemo_asr
        _model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
            model_name="nvidia/parakeet-tdt-1.1b"
        )
    return _model

# gpu_worker/main.py
@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    # Save to temp WAV, run model.transcribe(), return text
    ...

@app.get("/health")
async def health():
    model = get_model()
    return {"status": "ok", "model_loaded": model is not None}
```

### Pattern 3: Audio Lifecycle with Guaranteed Cleanup

**What:** Audio files are downloaded to a temp directory and cleaned up in a `finally` block, even on failure.

**When to use:** Every Pass 3 transcription that downloads audio.

**Example:**

```python
async def transcribe_via_gpu(url: str, duration_seconds: int | None) -> str | None:
    tmp_dir = os.environ.get("AUDIO_TMP_DIR", tempfile.gettempdir())
    audio_path = None
    wav_path = None
    try:
        # Download audio via yt-dlp
        audio_path = await download_audio(url, tmp_dir)
        # Convert to 16kHz mono WAV via ffmpeg
        wav_path = await convert_to_wav(audio_path, tmp_dir)
        # Send to GPU worker
        transcript = await gpu_client.transcribe(wav_path)
        return transcript
    finally:
        # TRANS-05: Audio never persisted
        for path in [audio_path, wav_path]:
            if path and os.path.exists(path):
                os.unlink(path)
```

### Pattern 4: Railway API GPU Scaling (Internal Scheduler)

**What:** A background task in the CPU worker loop checks `process_content` queue depth every 5 minutes and scales the GPU service via Railway GraphQL API.

**When to use:** Mirrors the existing `_reclamation_scheduler` pattern in worker/loop.py.

**Example:**

```python
# In worker/loop.py, add alongside _reclamation_scheduler:
async def _gpu_scaling_scheduler(
    session_factory, interval, shutdown_event, settings
):
    while not shutdown_event.is_set():
        await _interruptible_sleep(interval, shutdown_event)
        if shutdown_event.is_set():
            break
        async with session_factory() as session:
            depth = await get_queue_depth(session, "process_content")
            threshold = await get_config_value(session, "gpu_queue_threshold", 5)
            idle_minutes = await get_config_value(
                session, "gpu_idle_minutes_before_shutdown", 30
            )
            # Scale up/down via Railway API
            await manage_gpu_scaling(depth, threshold, idle_minutes)
```

### Anti-Patterns to Avoid

- **Loading NeMo model per-request:** The model takes 2-5 minutes to load into VRAM. Load once at startup, hold in module-level singleton, reuse across all requests.
- **Storing audio files permanently:** Spec is explicit -- audio is ephemeral. Always use temp files with cleanup in `finally` blocks.
- **Running yt-dlp as subprocess when Python API available:** yt-dlp has a full Python API via `yt_dlp.YoutubeDL`. Use it directly for both caption extraction and audio download.
- **Putting GPU transcription in the job queue:** The GPU worker should be a stateless HTTP service, not a queue consumer. This keeps model lifecycle management simple (load once, serve forever) and avoids coordinating queue claims across GPU replicas.
- **Blocking the async loop with ffmpeg:** Use `asyncio.create_subprocess_exec` for ffmpeg conversion, not `subprocess.run`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YouTube caption extraction | Custom YouTube API client | yt-dlp with `writeautomaticsub=True, skip_download=True` | Handles rate limits, format negotiation, auth cookies |
| Audio format conversion | Custom audio processing | ffmpeg subprocess (`-ar 16000 -ac 1 -acodec pcm_s16le`) | Battle-tested, handles all input formats, edge cases |
| WebVTT parsing | Regex on subtitle files | webvtt-py library | Handles timing overlap, caption dedup, encoding |
| ASR inference | Custom model loading | NeMo `from_pretrained` + `.transcribe()` | Handles tokenization, chunking, beam search |
| Railway API client | REST API wrapper | Direct GraphQL via httpx POST | Railway API is GraphQL-only, simple enough for 2 mutations |
| Temp file cleanup | Manual path tracking | `tempfile.NamedTemporaryFile(delete=False)` + `finally` | OS handles cleanup edge cases, no leaked files |

**Key insight:** The transcription pipeline is glue code connecting four mature tools (yt-dlp, ffmpeg, NeMo, Railway API). The value is in correct orchestration and failure handling, not in reimplementing any of these tools.

## Common Pitfalls

### Pitfall 1: yt-dlp Caption Extraction Returns Auto-Generated Garbage

**What goes wrong:** YouTube auto-generated captions can be low quality (< 100 words for a 1-hour video, or gibberish). The spec says "Rejected if < 100 words."
**Why it happens:** Some videos have auto-generated captions that are mostly timestamps or empty.
**How to avoid:** After extracting captions, count words. If `len(text.split()) < 100`, treat as failed and fall through to Pass 3.
**Warning signs:** Transcripts that are suspiciously short relative to content duration.

### Pitfall 2: ffmpeg Subprocess Hangs on Malformed Audio

**What goes wrong:** ffmpeg can hang indefinitely on corrupt or unusual audio streams.
**Why it happens:** No timeout on subprocess call.
**How to avoid:** Use `asyncio.wait_for` with a timeout (e.g., 5 minutes for audio conversion). Kill the subprocess on timeout.
**Warning signs:** Worker processes accumulating without completing.

### Pitfall 3: Temp Files Leaked on Worker Crash

**What goes wrong:** If the worker process is killed (SIGKILL, OOM) between download and cleanup, temp audio files accumulate.
**Why it happens:** `finally` blocks don't run on SIGKILL.
**How to avoid:** Use a dedicated `AUDIO_TMP_DIR` and add a startup cleanup that deletes any .wav/.mp3 files older than 1 hour. This is defense-in-depth beyond the `finally` block.
**Warning signs:** Disk usage growing on the worker volume.

### Pitfall 4: NeMo Model Loading Fails Silently on CPU

**What goes wrong:** `from_pretrained` downloads and loads the model. On a CPU-only machine it will be extremely slow or fail.
**Why it happens:** The model is designed for GPU inference.
**How to avoid:** The GPU worker health endpoint should verify `torch.cuda.is_available()` and that the model is loaded. CPU worker should never import NeMo.
**Warning signs:** GPU worker health check fails or responds slowly.

### Pitfall 5: Railway API Rate Limits on Free/Hobby Plans

**What goes wrong:** The `manage_gpu_scaling` task calls Railway API every 5 minutes. At 100 req/hour (free) or 1000 req/hour (hobby), this is fine, but combined with other API calls it could hit limits.
**Why it happens:** Multiple Railway API calls (scale check + scale up/down + status check).
**How to avoid:** Cache the last known GPU status in-memory. Only call Railway API when a state change is needed. Log rate limit headers.
**Warning signs:** `429` responses from Railway API.

### Pitfall 6: Long Audio Files Cause GPU OOM

**What goes wrong:** Audio files > 60 minutes can exceed GPU memory during NeMo inference.
**Why it happens:** The FastConformer attention mechanism scales with audio length.
**How to avoid:** Spec says "Files > 60 min chunked into 45-min segments." Split long WAV files into 45-minute segments with ffmpeg before sending to GPU. Concatenate transcripts.
**Warning signs:** CUDA OOM errors in GPU worker logs.

### Pitfall 7: yt-dlp Version Drift Breaks YouTube Extraction

**What goes wrong:** YouTube frequently changes its API, breaking older yt-dlp versions.
**Why it happens:** yt-dlp is pinned to 2025.12.08 per spec.
**How to avoid:** Pin the version strictly (`==2025.12.08`). If extraction starts failing, it's a known issue that requires a version bump -- not a code fix. Log the error category as `AUDIO_DOWNLOAD_FAILED`.
**Warning signs:** Sudden spike in `audio_download_failed` errors.

## Code Examples

Verified patterns from official sources and project conventions:

### yt-dlp Caption Extraction (Pass 1)

```python
# Source: yt-dlp Python API + project conventions
from yt_dlp import YoutubeDL

def extract_youtube_captions(video_url: str) -> str | None:
    """Extract YouTube auto-generated or manual captions.

    Returns plain text transcript or None if no captions available.
    Rejects transcripts with fewer than 100 words (spec Section 7.1).
    """
    opts = {
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": ["en"],
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        subs = info.get("requested_subtitles") or {}
        en_sub = subs.get("en")
        if not en_sub:
            return None

        # Download and parse the VTT file
        sub_url = en_sub.get("url")
        if not sub_url:
            return None

        # Fetch VTT content and parse to plain text
        # (implementation uses httpx + webvtt-py)
        text = _fetch_and_parse_vtt(sub_url)

        # Spec 7.1: Reject if < 100 words
        if text and len(text.split()) >= 100:
            return text
        return None
    except Exception:
        return None  # Fall through to next pass
```

### yt-dlp Audio Download

```python
# Source: yt-dlp Python API
import tempfile
from yt_dlp import YoutubeDL

def download_audio(url: str, tmp_dir: str) -> str:
    """Download audio to temp directory via yt-dlp.

    Returns path to the downloaded audio file.
    Raises on failure (worker categorizes as AUDIO_DOWNLOAD_FAILED).
    """
    output_path = os.path.join(tmp_dir, f"audio_{uuid.uuid4().hex[:12]}")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path + ".%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    # yt-dlp may change extension; find the actual file
    for ext in ["wav", "opus", "m4a", "mp3", "webm"]:
        candidate = f"{output_path}.{ext}"
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(f"Audio download produced no file at {output_path}")
```

### ffmpeg 16kHz Mono WAV Conversion

```python
# Source: ffmpeg documentation + asyncio subprocess pattern
import asyncio

async def convert_to_wav(input_path: str, tmp_dir: str) -> str:
    """Convert audio to 16kHz mono WAV for Parakeet.

    Uses asyncio subprocess to avoid blocking the event loop.
    Returns path to converted WAV file.
    """
    output_path = os.path.join(tmp_dir, f"converted_{uuid.uuid4().hex[:12]}.wav")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", input_path,
        "-ar", "16000",     # 16kHz sample rate
        "-ac", "1",         # mono
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-y",               # overwrite
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode()[:500]}")

    return output_path
```

### Railway GraphQL Scaling

```python
# Source: Railway API docs (docs.railway.com/integrations/api)
import httpx

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

async def scale_gpu_service(replicas: int) -> bool:
    """Scale the GPU worker service to the specified replica count.

    Uses Railway GraphQL API with serviceInstanceUpdate mutation.
    Returns True if successful.
    """
    api_key = os.environ.get("RAILWAY_API_KEY")
    service_id = os.environ.get("RAILWAY_GPU_SERVICE_ID")
    environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID")

    if not all([api_key, service_id, environment_id]):
        logger.warning("railway_config_missing")
        return False

    mutation = """
    mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
        serviceInstanceUpdate(
            serviceId: $serviceId,
            environmentId: $environmentId,
            input: $input
        )
    }
    """
    variables = {
        "serviceId": service_id,
        "environmentId": environment_id,
        "input": {"numReplicas": replicas},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RAILWAY_API_URL,
            json={"query": mutation, "variables": variables},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return "errors" not in data
```

### NeMo Parakeet Inference (GPU Worker)

```python
# Source: HuggingFace nvidia/parakeet-tdt-1.1b model card
import nemo.collections.asr as nemo_asr

# Module-level singleton -- loaded once, persisted in VRAM
_model = None

def load_model() -> nemo_asr.models.EncDecRNNTBPEModel:
    global _model
    if _model is None:
        _model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
            model_name="nvidia/parakeet-tdt-1.1b"
        )
        _model.eval()  # Set to inference mode
    return _model

def transcribe_audio(wav_path: str) -> str:
    """Transcribe a 16kHz mono WAV file using Parakeet TDT 1.1B.

    Returns lowercase English text.
    """
    model = load_model()
    output = model.transcribe([wav_path])
    return output[0].text
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Whisper for ASR | Parakeet TDT 1.1B | 2024 | ~6% WER vs ~8% for Whisper large-v3; TDT decoder provides significant inference speedup |
| youtube-dl | yt-dlp | 2021+ | Active maintenance, better extraction, Python API |
| Redis for GPU job dispatch | HTTP service + queue depth check | N/A (architectural choice) | Simpler architecture, no Redis dependency, model stays loaded |
| ffmpeg-python wrapper | Direct asyncio subprocess | N/A | Fewer dependencies, better async integration, clearer error handling |

**Deprecated/outdated:**
- `nemo_toolkit[all]`: The `[all]` extra pulls in unnecessary training dependencies. For inference-only, the base `nvcr.io/nvidia/nemo:24.05` image has everything needed.
- `youtube-dl`: Unmaintained. Always use `yt-dlp`.

## Open Questions

1. **Long Audio Chunking Strategy**
   - What we know: Spec says "Files > 60 min chunked into 45-min segments." NeMo supports buffered inference with configurable chunk/buffer sizes.
   - What's unclear: Whether to use NeMo's built-in buffered inference (via model parameters) or pre-split the WAV file with ffmpeg before sending to the GPU service. Pre-splitting is simpler and more predictable.
   - Recommendation: Use ffmpeg to split files > 60 minutes into 45-minute WAV segments. Send each segment separately. Concatenate transcripts. This keeps the GPU service simple (no special long-audio logic).

2. **GPU Worker Communication Protocol**
   - What we know: CPU worker needs to send audio to GPU worker for transcription.
   - What's unclear: Whether to send the audio file via multipart HTTP upload, or use a shared volume / object storage URL.
   - Recommendation: Multipart HTTP upload via internal Railway networking. Audio files are temporary and small relative to network bandwidth. Shared volumes add complexity. Internal networking is free on Railway.

3. **RAILWAY_ENVIRONMENT_ID Discovery**
   - What we know: `serviceInstanceUpdate` requires `environmentId`. Railway auto-injects some env vars but not necessarily this one.
   - What's unclear: Whether `RAILWAY_ENVIRONMENT_ID` is auto-injected or needs manual configuration.
   - Recommendation: Add `RAILWAY_ENVIRONMENT_ID` to `.env.example` and document in deployment notes. It can be found in the Railway dashboard URL.

4. **Existing Transcript Sources (Pass 2)**
   - What we know: Spec says "Check episode page for published transcript before downloading audio. Requires per-show config in `sources.config.transcript_url_pattern`."
   - What's unclear: What format these transcripts are in (HTML, plain text, structured). The spec says "Expanded as shows are onboarded."
   - Recommendation: Implement Pass 2 as a simple httpx GET to a URL derived from the content URL + pattern substitution, with HTML-to-text extraction. Keep it minimal for v1 -- most content will go through Pass 1 or Pass 3.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.25.x |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/unit/ -x -q` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TRANS-01 | Three-pass fallback: captions -> existing -> GPU | unit | `uv run pytest tests/unit/test_process_content.py -x` | No -- Wave 0 |
| TRANS-01 | Caption extraction returns text or None | unit | `uv run pytest tests/unit/test_captions.py -x` | No -- Wave 0 |
| TRANS-01 | Existing transcript fetch returns text or None | unit | `uv run pytest tests/unit/test_existing_transcript.py -x` | No -- Wave 0 |
| TRANS-02 | GPU worker /transcribe endpoint returns text | integration | `uv run pytest tests/integration/test_gpu_worker.py -x` | No -- Wave 0 (GPU-tagged, skipped by default) |
| TRANS-03 | Audio download produces file at expected path | unit | `uv run pytest tests/unit/test_audio.py::test_download -x` | No -- Wave 0 |
| TRANS-03 | ffmpeg converts to 16kHz mono WAV | unit | `uv run pytest tests/unit/test_audio.py::test_convert -x` | No -- Wave 0 |
| TRANS-04 | Scale up when queue depth > threshold | unit | `uv run pytest tests/unit/test_gpu_scaling.py::test_scale_up -x` | No -- Wave 0 |
| TRANS-04 | Scale down after idle timeout | unit | `uv run pytest tests/unit/test_gpu_scaling.py::test_scale_down -x` | No -- Wave 0 |
| TRANS-04 | Railway API called with correct GraphQL mutation | unit | `uv run pytest tests/unit/test_railway_client.py -x` | No -- Wave 0 |
| TRANS-05 | Temp files deleted on success | unit | `uv run pytest tests/unit/test_audio.py::test_cleanup_success -x` | No -- Wave 0 |
| TRANS-05 | Temp files deleted on failure | unit | `uv run pytest tests/unit/test_audio.py::test_cleanup_failure -x` | No -- Wave 0 |
| TRANS-06 | Content row updated with body_text, word_count, method | integration | `uv run pytest tests/integration/test_process_content.py -x` | No -- Wave 0 |
| TRANS-06 | process_content handler contract test | contract | `uv run pytest tests/contract/test_transcription_handlers.py -x` | No -- Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/unit/ -x -q`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_captions.py` -- covers TRANS-01 Pass 1 (yt-dlp caption extraction)
- [ ] `tests/unit/test_existing_transcript.py` -- covers TRANS-01 Pass 2
- [ ] `tests/unit/test_process_content.py` -- covers TRANS-01 three-pass orchestration
- [ ] `tests/unit/test_audio.py` -- covers TRANS-03, TRANS-05 (download, convert, cleanup)
- [ ] `tests/unit/test_gpu_scaling.py` -- covers TRANS-04 (scale up/down logic)
- [ ] `tests/unit/test_railway_client.py` -- covers TRANS-04 (Railway API calls)
- [ ] `tests/integration/test_process_content.py` -- covers TRANS-06 (DB updates)
- [ ] `tests/integration/test_gpu_worker.py` -- covers TRANS-02 (GPU-tagged, skipped by default)
- [ ] `tests/contract/test_transcription_handlers.py` -- covers TRANS-06 contract
- [ ] ffmpeg must be available in test environment (already on most systems, add to CI if needed)

## Sources

### Primary (HIGH confidence)

- [HuggingFace nvidia/parakeet-tdt-1.1b](https://huggingface.co/nvidia/parakeet-tdt-1.1b) -- Model class (`EncDecRNNTBPEModel`), `from_pretrained` API, input requirements (16kHz mono WAV), output format (lowercase text), Python usage
- [Railway API docs](https://docs.railway.com/integrations/api) -- GraphQL endpoint URL (`backboard.railway.com/graphql/v2`), auth header format (`Bearer`), rate limits (100/hr free, 1000/hr hobby, 10K/hr pro)
- [Railway Manage Services](https://docs.railway.com/integrations/api/manage-services) -- `serviceInstanceUpdate` mutation, `numReplicas` parameter, `serviceInstance` query
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp) -- Python API (YoutubeDL class), `writeautomaticsub`, `skip_download`, `FFmpegExtractAudio` postprocessor
- Project codebase: ThinkTank_Specification.md Sections 6.5, 7.1-7.4 -- authoritative spec for pipeline behavior

### Secondary (MEDIUM confidence)

- [yt-dlp GitHub Issue #10561](https://github.com/yt-dlp/yt-dlp/issues/10561) -- Caption extraction via `extract_info(download=False)` returns `requested_subtitles` with VTT URLs
- [SaladCloud Parakeet Benchmark](https://blog.salad.com/parakeet-tdt-1-1b/) -- ~7GB VRAM for inference on files > 15 minutes (cross-verified with HF discussion)
- [NeMo Buffered Inference](https://github.com/NVIDIA-NeMo/NeMo/blob/main/examples/asr/asr_chunked_inference/) -- Chunked inference support for long audio files

### Tertiary (LOW confidence)

- Railway `serviceInstanceUpdate` exact behavior for scaling to 0 replicas -- documentation confirms `numReplicas` parameter exists but does not explicitly describe scale-to-zero semantics. Railway blog confirms scale-to-zero is supported for on-demand services. Needs validation during implementation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- yt-dlp, NeMo, ffmpeg are well-documented; Railway API documented via GraphQL schema
- Architecture: HIGH -- Follows existing project patterns (handler protocol, registry, scheduler); GPU-as-HTTP-service is a clean separation
- Pitfalls: HIGH -- Based on known issues with subprocess management, GPU memory, and yt-dlp version pinning
- Railway scaling: MEDIUM -- GraphQL mutation confirmed, but scale-to-zero semantics need runtime validation

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (30 days -- stable libraries, pinned versions)
