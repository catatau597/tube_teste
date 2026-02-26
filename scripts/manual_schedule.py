#!/usr/bin/env python3
"""
Script utilitário para agendamento manual (placeholder).
Uso:
    python scripts/manual_schedule.py --db /caminho/para/db
"""
import argparse
#from core.scheduler import Scheduler
#from core.config import AppConfig
#from core.youtube_api import YouTubeAPI
#from core.state_manager import StateManager

parser = argparse.ArgumentParser(description="Executa agendamento manual (placeholder)")
parser.add_argument("--db", type=str, required=True, help="Caminho do banco de dados de configuração.")
args = parser.parse_args()

#cfg = AppConfig(db_path=args.db)
#yt = YouTubeAPI(api_key="dummy")
#sm = StateManager(cfg)
#sched = Scheduler(cfg, yt, sm)
#print("[INFO] Agendamento executado (placeholder)")
print("[INFO] Agendamento manual executado (placeholder)")
