FROM python:3.11-slim

# Install only required runtimes — keep image minimal
RUN apt-get update && apt-get install -y \
    nodejs npm ruby-full bash \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Common Python libraries agents are likely to need
RUN pip install --no-cache-dir \
    requests numpy pandas matplotlib

# Non-root user for extra isolation
RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /sandbox

# Default: do nothing (command passed at runtime)
CMD ["bash"]
