#!/usr/bin/env bash
set -euo pipefail

echo "🔧 Applying GitHub HTTPS clone fix for Docker builds..."

# Update packages en installeer git en certs
sudo apt-get update -y
sudo apt-get install -y git ca-certificates

# Forceer git om HTTPS te gebruiken (geen login prompt)
git config --global --add safe.directory /app
git config --global url."https://github.com/".insteadOf "git@github.com:"
git config --global url."https://".insteadOf "git://"

echo "✅ Git config fixed! Rebuild Docker image now:"
echo "   sudo docker compose build backend --no-cache && sudo docker compose up -d"
