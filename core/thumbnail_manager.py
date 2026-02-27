"""
core/thumbnail_manager.py
Responsabilidade: Download, cache local e exclusao de thumbnails por video_id.
Lifecycle: thumbnail nasce com o stream no StateManager e morre junto com ele.
"""
import logging
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("TubeWrangler")


class ThumbnailManager:
    def __init__(self, cache_dir: str):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_local_path(self, video_id: str) -> Path:
        return self._cache_dir / f"{video_id}.jpg"

    def get_url(self, video_id: str, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/api/thumbnail/{video_id}"

    def ensure_cached(self, video_id: str, remote_url: str) -> bool:
        """Baixa thumbnail se ainda nao existir em cache. Retorna True se ok."""
        local = self.get_local_path(video_id)
        if local.exists():
            return True
        if not remote_url:
            return False
        try:
            urllib.request.urlretrieve(remote_url, local)
            logger.debug(f"Thumbnail cacheada: {video_id}")
            return True
        except Exception as e:
            logger.warning(f"Falha ao cachear thumbnail {video_id}: {e}")
            return False

    def delete(self, video_id: str) -> None:
        """Remove thumbnail do cache. Chamado quando stream e removido do StateManager."""
        local = self.get_local_path(video_id)
        if local.exists():
            try:
                local.unlink()
                logger.debug(f"Thumbnail removida: {video_id}")
            except Exception as e:
                logger.warning(f"Falha ao remover thumbnail {video_id}: {e}")

    def serve(self, video_id: str) -> Optional[bytes]:
        """Retorna bytes da thumbnail local ou None se nao existir."""
        local = self.get_local_path(video_id)
        if local.exists():
            return local.read_bytes()
        return None
