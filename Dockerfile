FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (gosu for privilege dropping)
RUN apt-get update && apt-get install -y \
    curl \
    gosu \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Create workspace directories
RUN mkdir -p workspace/output workspace/memory workspace/skills

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app/workspace

# Copy entrypoint (runs as root to fix perms, then drops to appuser)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8765

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
