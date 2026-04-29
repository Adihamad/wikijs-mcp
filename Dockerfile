# ── Wiki.js MCP Server ─────────────────────────────────────────────────────────
FROM python:3.12-slim

# Keeps Python from buffering stdout/stderr (good for Docker logs)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server
COPY server.py .

# Port the MCP SSE server listens on
EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/sse')" || exit 1

CMD ["python", "server.py"]
