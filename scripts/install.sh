#!/usr/bin/env bash
set -euo pipefail

echo '=== CrewAI Agent Team — Installation Script ==='
echo 'This script sets up the complete system. It will take 5-10 minutes.'

# 1. Create project directories
mkdir -p workspace/{output,memory,skills}

if [ ! -f workspace/skills/learning_queue.md ]; then
    echo '# Learning Queue — add one topic per line' > workspace/skills/learning_queue.md
    echo 'CrewAI multi-agent patterns' >> workspace/skills/learning_queue.md
    echo 'Python async programming best practices' >> workspace/skills/learning_queue.md
fi

# 2. Copy .env.example to .env if .env does not exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ''
    echo 'ACTION REQUIRED: Open .env and fill in your API keys before continuing.'
    echo 'Required: ANTHROPIC_API_KEY, BRAVE_API_KEY, SIGNAL_BOT_NUMBER, SIGNAL_OWNER_NUMBER, GATEWAY_SECRET'
    echo ''
    read -p 'Press Enter when you have filled in .env...'
fi

# 3. Create Python virtual environment and install dependencies
echo 'Creating Python virtual environment...'
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Build the sandbox Docker image
echo 'Building sandbox Docker image...'
docker build -t crewai-sandbox:latest sandbox/

# 5. Pull the ChromaDB Docker image
echo 'Pulling ChromaDB image...'
docker pull chromadb/chroma:latest

# 6. Start Docker Compose services
echo 'Starting Docker Compose services...'
docker compose up -d

echo ''
echo '=== Installation complete ==='
echo 'Next steps:'
echo '1. Set up signal-cli: register your bot number (see README)'
echo '2. Set up Tailscale: sudo tailscale up && sudo tailscale serve --bg 8765'
echo '3. Run: bash scripts/health_check.sh'
echo '4. Send a Signal message from your iPhone to test'
