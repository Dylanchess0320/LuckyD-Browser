# ── Multi-stage Docker Build for LuckyD Code ──────────────────────────
# Build: docker build -t luckyd-code .
# Run:   docker run -it --rm -v "$(pwd):/workspace" --env-file .env luckyd-code
#
# Stages:
#   1. builder  — installs all deps and tools
#   2. runtime  — minimal production image

# ═══════════════════════════════════════════════════════════════════════
# Stage 1: Builder
# ═══════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

LABEL stage="builder"
LABEL description="LuckyD Code build stage"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt

# Copy source code
COPY . .

# ═══════════════════════════════════════════════════════════════════════
# Stage 2: Runtime (minimal)
# ═══════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

LABEL maintainer="LuckyD <dev@luckyd.ai>"
LABEL description="LuckyD Code — AI-powered coding agent"
LABEL version="2.2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    CODING_AGENT_HOME=/workspace

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent -m -d /home/agent agent

# Copy Python packages from builder
COPY --from=builder /root/.local /home/agent/.local
COPY --from=builder /build /app

# Create workspace directory
RUN mkdir -p /workspace && chown agent:agent /workspace /app

WORKDIR /workspace

# Add local Python bin to PATH
ENV PATH=/home/agent/.local/bin:$PATH

# Switch to non-root user
USER agent

# Default command: interactive REPL
ENTRYPOINT ["python", "/app/main.py"]
CMD [""]

# ── Health check ─────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1
