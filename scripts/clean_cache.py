#!/usr/bin/env python3
"""
Script utilitário para limpeza do cache de estado do StateManager.
Uso:
    python scripts/clean_cache.py [--cache /caminho/para/cache]
"""
import argparse
from core.config import AppConfig
from core.state_manager import StateManager
from pathlib import Path

parser = argparse.ArgumentParser(description="Limpa o arquivo de cache do StateManager.")
parser.add_argument("--cache", type=str, default=None, help="Caminho do arquivo de cache a ser removido.")
parser.add_argument("--db", type=str, default=None, help="Caminho do banco de dados de configuração.")
args = parser.parse_args()

cfg = AppConfig(db_path=args.db) if args.db else AppConfig()
sm = StateManager(cfg, cache_path=Path(args.cache) if args.cache else None)

if sm.cache_path.exists():
    sm.cache_path.unlink()
    print(f"Arquivo de cache removido: {sm.cache_path}")
else:
    print(f"Arquivo de cache não encontrado: {sm.cache_path}")
