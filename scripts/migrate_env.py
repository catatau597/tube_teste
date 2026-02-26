"""
scripts/migrate_env.py
----------------------
Migração única: importa file.env → /data/config.db
Executar UMA VEZ:
    docker compose exec tubewranglerr python3 scripts/migrate_env.py
"""
from pathlib import Path
import sys
sys.path.insert(0, "/app")
from core.config import AppConfig

if __name__ == "__main__":
    print("Iniciando migração .env → SQLite...")
    cfg = AppConfig()
    cfg.import_from_env_file(Path("/app/file.env"))
    print("\nConfiguração atual por seção:")
    for section, rows in cfg.get_all_by_section().items():
        print(f"\n[{section}]")
        for row in rows:
            val = row["value"]
            display = val[:60] + "..." if len(val) > 60 else val
            print(f"  {row['key']} = {display}")
