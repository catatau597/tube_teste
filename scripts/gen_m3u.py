#!/usr/bin/env python3
"""
Script utilitário para geração de playlist M3U manual.
Uso:
    python scripts/gen_m3u.py --db /caminho/para/db
"""
import argparse
from core.config import AppConfig
from core.playlist_builder import M3UGenerator

parser = argparse.ArgumentParser(description="Gera playlist M3U manualmente.")
parser.add_argument("--db", type=str, required=True, help="Caminho do banco de dados de configuração.")
args = parser.parse_args()

cfg = AppConfig(db_path=args.db)
m3u = M3UGenerator(cfg)
# Placeholder: lógica real de geração será implementada na próxima etapa
print("[INFO] Playlist M3U gerada (placeholder)")
