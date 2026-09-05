# Docker & Sandboxing

LuckyD Code supports running tools and agent workloads inside isolated container sandboxes to guarantee host system security.

---

## Quick Start with Docker Compose

```bash
# Build and start containerized agent
docker compose up -d

# Attach to agent container
docker compose exec luckyd-agent python main.py
```

## Security Boundaries
- Read-only root filesystem mounting options.
- Command blocklist for high-risk system commands.
- Sandboxed workspace mounting strictly restricted to the project root.
