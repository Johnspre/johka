#!/usr/bin/env bash
set -euo pipefail

############################################
# JOHKA LIVE - FULL AUTO FIX SCRIPT
# ------------------------------------------
# Stopt, herbouwt en checkt alle containers.
# Geeft visuele status in kleur.
############################################

INFRA_DIR="/opt/johka-live/infra"
ENV_FILE="/opt/johka-live/.env"

# --- Kleuren ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # geen kleur

echo ""
echo -e "${CYAN}🧹 [1/6] Containers stoppen en verwijderen...${NC}"
cd "$INFRA_DIR"
sudo docker compose down -v --remove-orphans || true

echo ""
echo -e "${CYAN}🔥 [2/6] Docker opschonen...${NC}"
sudo docker system prune -af || true

echo ""
echo -e "${CYAN}🔍 [3/6] Controleren op .env fouten...${NC}"

if grep -q '"abc123' "$ENV_FILE"; then
  echo -e "${YELLOW}⚠️  Quotes gevonden in LIVEKIT_KEYS — we verwijderen ze...${NC}"
  sudo sed -i 's/"abc123/abc123/' "$ENV_FILE"
  sudo sed -i 's/FYx1sDo="/FYx1sDo=/' "$ENV_FILE"
fi

if grep -q 'LIVEKIT_KEYS=' "$ENV_FILE"; then
  echo -e "${GREEN}✅ LIVEKIT_KEYS aanwezig in .env${NC}"
else
  echo -e "${RED}❌ LIVEKIT_KEYS ontbrak — automatisch toegevoegd${NC}"
  echo 'LIVEKIT_KEYS=abc123 n3QTgVWu2FXY4w+8QyJ6fQZEyBpr6ZfXVbFYx1sDo=' | sudo tee -a "$ENV_FILE"
fi

echo ""
echo -e "${CYAN}🛠️ [4/6] Containers opnieuw bouwen...${NC}"
sudo docker compose build --no-cache

echo ""
echo -e "${CYAN}🚀 [5/6] Containers starten...${NC}"
sudo docker compose up -d

echo ""
echo -e "${CYAN}📊 [6/6] Health check status:${NC}"
sleep 6

# Controleer gezondheid
services=(postgres redis livekit backend)
for s in "${services[@]}"; do
  status=$(sudo docker inspect --format='{{.State.Health.Status}}' "infra-$s" 2>/dev/null || echo "none")
  if [[ "$status" == "healthy" ]]; then
    echo -e "   ${GREEN}✔ $s is healthy${NC}"
  elif [[ "$status" == "starting" ]]; then
    echo -e "   ${YELLOW}⏳ $s is starting${NC}"
  elif [[ "$status" == "unhealthy" ]]; then
    echo -e "   ${RED}❌ $s is unhealthy${NC}"
  else
    running=$(sudo docker ps --filter "name=infra-$s" --format '{{.Status}}')
    if [[ -z "$running" ]]; then
      echo -e "   ${RED}❌ $s is not running${NC}"
    else
      echo -e "   ${YELLOW}ℹ️  $s status: $running${NC}"
    fi
  fi
done

echo ""
echo -e "${CYAN}💡 Klaar! Gebruik 'sudo docker compose logs -f livekit' om LiveKit live te volgen.${NC}"

