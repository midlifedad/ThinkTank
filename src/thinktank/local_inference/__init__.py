"""Native Apple Silicon inference service (Option C, 2026-07-12).

Runs OUTSIDE Docker on the Mac Studio host: parakeet-mlx needs Metal and
pyannote wants MPS, neither of which exists inside Docker's Linux VM on
macOS. The dockerized pull-worker reaches this service via
host.docker.internal, mirroring the Railway worker-cpu -> worker-gpu
topology exactly.
"""
