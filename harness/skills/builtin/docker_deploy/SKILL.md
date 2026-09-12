---
name: docker_deploy
description: Docker containerization, multi-stage Dockerfiles, docker-compose orchestration, and deployment automation.
triggers: [docker, dockerfile, compose, container, image, deploy, kubernetes]
---
# Docker Deploy for Harness

Best practices for containerization, image minimization, and container security.

## Best Practices
1. **Multi-Stage Builds**:
   - Separate build-time tooling and dependencies from the minimal runtime image (e.g. `python:3.12-slim` or `alpine`).
2. **Layer Caching Optimization**:
   - Copy dependency manifests (`requirements.txt`, `package.json`, `Cargo.toml`) and install packages BEFORE copying the rest of application code.
3. **Security**:
   - Never run as root (`USER appuser`).
   - Use `.dockerignore` to exclude `.git`, `node_modules`, `.env`, temporary cache files.
