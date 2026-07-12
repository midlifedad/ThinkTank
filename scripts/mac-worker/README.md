# Mac Studio Transcription Worker (Option C)

Local, zero-marginal-cost transcription with speaker diarization, running
on an idle Apple Silicon machine alongside the Railway deployment.

## Topology

```
Railway Postgres (queue)
        ▲ outbound poll
        │
┌───────┴─────────────────────────── Mac Studio ───────────────┐
│  Docker container            NATIVE launchd service          │
│  ┌─────────────────────┐     ┌─────────────────────────────┐ │
│  │ thinktank worker    │ WAV │ local_inference (uvicorn)   │ │
│  │ claims              │────▶│ parakeet-mlx (Metal ASR)    │ │
│  │ process_content,    │HTTP │ pyannote (MPS diarization)  │ │
│  │ downloads + ffmpeg  │◀────│ 127.0.0.1:8765              │ │
│  └─────────────────────┘text └─────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

Inference is native because Docker on macOS runs a Linux VM with no
Metal/MPS/ANE access. The split mirrors production (worker-cpu →
worker-gpu over HTTP) — same client code, same `/transcribe` contract.

## Why whole episodes, no chunking

The worker sends full episodes in one request
(`GPU_LONG_AUDIO_THRESHOLD_SECONDS=36000`). Chunked requests would run
diarization per chunk and reset speaker identities at every boundary;
global diarization keeps `Speaker A` meaning one person for the whole
episode. Output format matches the AssemblyAI pass (`Speaker A: ...`).

## Setup

1. **HuggingFace token**: create at hf.co/settings/tokens and accept the
   terms of `pyannote/speaker-diarization-3.1` (gated). Store it:
   `swarmify keys add ThinkTank HF_TOKEN "hf_xxx" "pyannote gated model"`.
2. **Database URL**: deploy-ops issues the Railway Postgres *external*
   connection string. `cp scripts/mac-worker/mac-worker.env.example
   scripts/mac-worker/mac-worker.env` and fill it in (asyncpg scheme).
3. **Install**: `HF_TOKEN=hf_xxx ./scripts/mac-worker/install.sh`
   First run downloads ~2 GB of models; the script waits for `/health`.

## Operations

- Inference logs: `var/local-inference.log` / `.err.log`
- Worker logs: `docker compose -f docker/compose.mac-worker.yml logs -f`
- Pause: `docker compose -f docker/compose.mac-worker.yml stop`
  (queue simply accumulates; Railway reclaims any in-flight job after
  its stale timeout, and the AssemblyAI flag remains available for
  cloud bursts)
- The worker claims **only** `process_content`; all other job types stay
  on Railway workers.
- Sleep/reboot safe: launchd restarts the service (`KeepAlive`), Docker
  restarts the container (`unless-stopped`), interrupted jobs are
  reclaimed by the queue's stale-job scheduler.

## Cost

Electricity. ASR at hundreds× real-time on M-series Metal; pyannote
diarization is the slower stage (~10-20× real-time on MPS). A 2-hour
episode lands in roughly 6-12 minutes end-to-end.
