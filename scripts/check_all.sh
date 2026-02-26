#!/bin/bash
# Script utilitário para rodar todos os testes e lint dentro do container Docker
set -e
docker compose exec tubewranglerr python3 -m pytest tests/ -v
docker compose exec tubewranglerr pip install --quiet flake8
docker compose exec tubewranglerr flake8 core/ scripts/
echo "[OK] Testes e lint finalizados dentro do container."
