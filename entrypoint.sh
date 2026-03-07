#!/bin/bash
set -e

# Inicia Redis em background
echo "[entrypoint] Iniciando redis-server..."
redis-server --daemonize yes --save 60 1 --loglevel warning

# Aguarda Redis ficar pronto
echo "[entrypoint] Aguardando Redis..."
for i in {1..10}; do
  if redis-cli ping > /dev/null 2>&1; then
    echo "[entrypoint] Redis pronto!"
    break
  fi
  if [ $i -eq 10 ]; then
    echo "[entrypoint] ERRO: Redis não respondeu após 10 tentativas"
    exit 1
  fi
  sleep 1
done

# Inicia aplicação
echo "[entrypoint] Iniciando uvicorn..."
exec python3 -m uvicorn web.main:app --host 0.0.0.0 --port 8888
