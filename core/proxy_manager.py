"""
proxy_manager.py — Gerenciamento de streams e clientes com Redis.

Arquitetura:
  subprocess (ffmpeg/streamlink)
      |  stdout
      v
  StreamBuffer (Redis, chunks de ~1MB)
      |  get_chunk(index)
      v
  N clientes via stream_to_client() (async generator)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional

from core.stream_buffer import StreamBuffer
from core.config import AppConfig

logger = logging.getLogger("TubeWrangler.proxy")

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

CHUNK_SIZE               = 65536   # 64 KB por chunk de leitura do stdout (fallback)
CLIENT_TIMEOUT_S         = 30      # segundos sem receber dado → desconecta cliente
STREAM_IDLE_STOP_S       = 30      # segundos sem clientes → para o processo
INIT_TIMEOUT_S           = 15      # segundos aguardando primeiro chunk
MAX_STREAM_MISSES        = 10      # máximo de chunks consecutivos não encontrados antes de desconectar

# Duração em segundos de cada segmento do placeholder antes de encerrar naturalmente.
# Deve ser igual ao -t passado ao ffmpeg em build_ffmpeg_placeholder_cmd().
PLACEHOLDER_SEGMENT_DURATION = 30

# ---------------------------------------------------------------------------
# Modo debug global
# ---------------------------------------------------------------------------

_debug_enabled: bool = False


def set_debug_mode(enabled: bool) -> None:
    """Ativa/desativa modo debug detalhado de streaming.
    
    Quando ativado:
    - Logs verbose de buffer state (chunks, índices, MB)
    - Métricas de clientes atrasados e stalls
    - Estatísticas de taxa de crescimento do buffer
    """
    global _debug_enabled
    _debug_enabled = enabled
    logger.info(f"Modo debug de streaming: {'ATIVADO' if enabled else 'DESATIVADO'}")


def get_debug_mode() -> bool:
    """Retorna True se modo debug está ativo."""
    return _debug_enabled


# ---------------------------------------------------------------------------
# ClientInfo / ClientManager
# ---------------------------------------------------------------------------

@dataclass
class ClientInfo:
    client_id:    str
    ip:           str
    user_agent:   str
    connected_at: float
    last_active:  float
    bytes_sent:   int = 0
    current_index: int = 0  # índice atual do cliente no buffer
    stall_start: Optional[float] = None  # timestamp quando começou stall


class ClientManager:
    """Rastreia clientes conectados a um único stream."""

    def __init__(self, video_id: str) -> None:
        self.video_id = video_id
        self._clients: Dict[str, ClientInfo] = {}
        self._lock    = threading.Lock()
        self._last_disconnect: Optional[float] = None

    def add_client(self, client_id: str, ip: str, user_agent: str) -> None:
        with self._lock:
            self._clients[client_id] = ClientInfo(
                client_id    = client_id,
                ip           = ip,
                user_agent   = user_agent,
                connected_at = time.time(),
                last_active  = time.time(),
            )
        logger.info(f"[{self.video_id}] cliente conectado: {client_id} ({ip})  total={self.count}")

    def remove_client(self, client_id: str) -> int:
        with self._lock:
            info = self._clients.pop(client_id, None)
            remaining = len(self._clients)
            if remaining == 0:
                self._last_disconnect = time.time()
        if info:
            duration = time.time() - info.connected_at
            mb_sent = info.bytes_sent / 1024 / 1024
            bps = info.bytes_sent / duration if duration > 0 else 0
            kbps = bps / 1024
            
            log_msg = (
                f"[{self.video_id}] cliente desconectado: {client_id}  "
                f"duração={duration:.1f}s  MB={mb_sent:.2f}  restantes={remaining}"
            )
            if _debug_enabled:
                log_msg += f"  kbps={kbps:.1f}"
            logger.info(log_msg)
        return remaining

    def update_activity(self, client_id: str, bytes_sent: int, current_index: int = 0) -> None:
        with self._lock:
            if client_id in self._clients:
                self._clients[client_id].last_active = time.time()
                self._clients[client_id].bytes_sent  = bytes_sent
                self._clients[client_id].current_index = current_index
                # Reset stall se cliente está ativo
                self._clients[client_id].stall_start = None

    def mark_stall(self, client_id: str) -> None:
        """Marca que cliente entrou em stall (sem receber chunks)."""
        with self._lock:
            if client_id in self._clients and self._clients[client_id].stall_start is None:
                self._clients[client_id].stall_start = time.time()
                if _debug_enabled:
                    logger.warning(f"[{self.video_id}] 🚫 Stall detectado: {client_id}")

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def idle_since(self) -> Optional[float]:
        """Retorna timestamp do último cliente desconectado (ou None se há clientes)."""
        with self._lock:
            if self._clients:
                return None
            return self._last_disconnect

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "client_id":    c.client_id,
                    "ip":           c.ip,
                    "connected_at": c.connected_at,
                    "bytes_sent":   c.bytes_sent,
                }
                for c in self._clients.values()
            ]
    
    def debug_snapshot(self, buffer_index: int) -> List[dict]:
        """Retorna snapshot detalhado para debug."""
        with self._lock:
            now = time.time()
            result = []
            for c in self._clients.values():
                duration = now - c.connected_at
                bps = c.bytes_sent / duration if duration > 0 else 0
                lag = buffer_index - c.current_index
                stall_time = now - c.stall_start if c.stall_start else 0
                
                result.append({
                    "client_id": c.client_id,
                    "ip": c.ip,
                    "connected_at": c.connected_at,
                    "duration_s": round(duration, 1),
                    "bytes_sent": c.bytes_sent,
                    "kbps": round(bps / 1024, 1),
                    "current_index": c.current_index,
                    "lag_chunks": lag,
                    "is_stalled": c.stall_start is not None,
                    "stall_time_s": round(stall_time, 1) if stall_time > 0 else 0,
                })
            return result


# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------

# video_id → objeto
_buffers:   Dict[str, StreamBuffer]  = {}
_managers:  Dict[str, ClientManager] = {}
_processes: Dict[str, subprocess.Popen] = {}
_process_start_times: Dict[str, float] = {}  # video_id → timestamp de start
_stream_reading: Dict[str, bool] = {}  # video_id → True enquanto thread stdout ativa

# Registra comandos de placeholder para permitir restart automático.
# video_id → List[str] cmd (somente para streams do tipo placeholder)
_placeholder_cmds: Dict[str, List[str]] = {}


# ---------------------------------------------------------------------------
# start_stream_reader
# ---------------------------------------------------------------------------

async def start_stream_reader(video_id: str, cmd: List[str]) -> subprocess.Popen:
    """
    Inicia o processo (ffmpeg/streamlink) e uma thread daemon que lê
    stdout → StreamBuffer (Redis).  Também loga stderr via logging.

    Cria StreamBuffer e ClientManager se ainda não existirem.
    """
    config = AppConfig().get_all()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

    if video_id not in _buffers:
        _buffers[video_id] = StreamBuffer(video_id, redis_url, {
            "buffer_chunk_size_kb": int(config.get("buffer_chunk_size_kb", 1024)),
            "buffer_chunk_ttl":     int(config.get("buffer_chunk_ttl", 90)),
            "buffer_max_memory_mb": int(config.get("buffer_max_memory_mb", 50)),
        })
        await _buffers[video_id].initialize()
        _managers[video_id] = ClientManager(video_id=video_id)

    _stream_reading[video_id] = True

    stdout_chunk_size = int(config.get("stream_read_chunk_kb", 64)) * 1024

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    _processes[video_id] = process
    _process_start_times[video_id] = time.time()

    cmd_str = " ".join(cmd)
    logger.info(f"[{video_id}] processo iniciado  PID={process.pid}")
    logger.debug(f"[{video_id}] cmd completo: {cmd_str}")

    loop = asyncio.get_event_loop()
    buf  = _buffers[video_id]

    # Thread: lê stdout → buffer Redis
    def _read_stdout() -> None:
        chunk_count = 0
        try:
            while True:
                chunk = process.stdout.read(stdout_chunk_size)
                if not chunk:
                    break
                future = asyncio.run_coroutine_threadsafe(buf.add_data(chunk), loop)
                future.result()
                chunk_count += 1

                # Debug: log a cada 100 chunks lidos
                if _debug_enabled and chunk_count % 100 == 0:
                    mb = chunk_count * stdout_chunk_size / 1024 / 1024
                    logger.info(
                        f"[{video_id}] 💾 stdout thread: {chunk_count} chunks lidos "
                        f"({mb:.2f} MB)"
                    )
        except Exception as exc:
            logger.error(f"[{video_id}] erro lendo stdout: {exc}")
        finally:
            _stream_reading[video_id] = False
            logger.info(f"[{video_id}] stdout encerrado (index={buf.head_index})")

    # Thread: loga TODO o stderr em INFO (não só erros)
    def _read_stderr() -> None:
        try:
            for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                low = line.lower()
                if any(kw in low for kw in ("error", "fail", "fatal", "invalid", "no playable", "unable")):
                    logger.warning(f"[{video_id}] stderr: {line}")
                else:
                    logger.info(f"[{video_id}] stderr: {line}")
        except Exception as exc:
            logger.debug(f"[{video_id}] stderr thread encerrada: {exc}")

    threading.Thread(target=_read_stdout, daemon=True, name=f"stdout-{video_id}").start()
    threading.Thread(target=_read_stderr, daemon=True, name=f"stderr-{video_id}").start()

    return process


# ---------------------------------------------------------------------------
# register_placeholder / restart_placeholder_if_needed
# ---------------------------------------------------------------------------

def register_placeholder(video_id: str, cmd: List[str]) -> None:
    """Registra o comando de placeholder para permitir restart automático.

    Deve ser chamado pelo caller (web/main.py) logo após start_stream_reader()
    quando o comando for um placeholder (ffmpeg -loop 1 -t N).

    Args:
        video_id: ID do vídeo YouTube.
        cmd:      Comando completo passado ao start_stream_reader().
    """
    _placeholder_cmds[video_id] = cmd
    logger.debug(f"[{video_id}] placeholder registrado para restart automático")


async def restart_placeholder_if_needed(video_id: str) -> bool:
    """Reinicia o processo placeholder se ele encerrou e ainda há clientes.

    O processo placeholder usa ffmpeg com -loop 1 -t PLACEHOLDER_SEGMENT_DURATION,
    portanto encerra normalmente após ~30s. Este método deve ser chamado
    periodicamente (ex: pelo loop do async generator de cada cliente) para
    garantir continuidade do stream enquanto houver audiência.

    Retorna True se um novo processo foi iniciado, False caso contrário.

    Não reinicia se:
    - O stream não é um placeholder (não está em _placeholder_cmds).
    - O processo ainda está rodando (poll() is None).
    - Não há clientes conectados (evita iniciar sem audiência).
    - O stream foi removido (stop_stream chamado).
    """
    if video_id not in _placeholder_cmds:
        return False

    mgr = _managers.get(video_id)
    if mgr is None or mgr.count == 0:
        # Sem clientes — não reinicia; stop_stream cuidará da limpeza
        return False

    proc = _processes.get(video_id)
    if proc is not None and proc.poll() is None:
        # Processo ainda vivo — nenhuma ação necessária
        return False

    cmd = _placeholder_cmds[video_id]
    logger.info(
        f"[{video_id}] placeholder encerrou naturalmente, reiniciando "
        f"(clientes={mgr.count})  cmd={cmd[0]}"
    )
    await start_stream_reader(video_id, cmd)
    return True


# ---------------------------------------------------------------------------
# stop_stream
# ---------------------------------------------------------------------------

async def stop_stream(video_id: str) -> None:
    """Para o processo e remove os recursos do stream."""
    # Remove registro de placeholder antes de qualquer outra coisa
    _placeholder_cmds.pop(video_id, None)

    proc = _processes.pop(video_id, None)
    if proc:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception as exc:
            logger.warning(f"[{video_id}] erro ao terminar processo: {exc}")
        logger.info(f"[{video_id}] processo encerrado  PID={proc.pid}")

    buf = _buffers.pop(video_id, None)
    if buf:
        try:
            await buf.cleanup()
        except Exception as exc:
            logger.warning(f"[{video_id}] erro ao limpar buffer: {exc}")

    _managers.pop(video_id, None)
    _process_start_times.pop(video_id, None)
    _stream_reading.pop(video_id, None)
    logger.info(f"[{video_id}] recursos liberados")


# ---------------------------------------------------------------------------
# stream_to_client
# ---------------------------------------------------------------------------

async def stream_to_client(
    video_id: str, client_id: str, start_from_live: bool = False
) -> AsyncGenerator[bytes, None]:
    """Async generator que entrega chunks do Redis para um cliente.

    Args:
        video_id:        ID do vídeo.
        client_id:       ID único do cliente conectado.
        start_from_live: Se True, inicia no índice atual (live). Se False, inicia do índice 0.
    """
    buf = _buffers[video_id]
    mgr = _managers[video_id]

    if start_from_live:
        client_index = await buf.get_current_index()
    else:
        client_index = 0

    consecutive_misses = 0

    while True:
        chunk = await buf.get_chunk(client_index + 1)

        if chunk:
            yield chunk
            client_index += 1
            consecutive_misses = 0
            mgr.update_activity(client_id, len(chunk), client_index)
        else:
            consecutive_misses += 1
            if consecutive_misses >= MAX_STREAM_MISSES:
                break
            await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# is_stream_active
# ---------------------------------------------------------------------------

def is_stream_active(video_id: str) -> bool:
    proc = _processes.get(video_id)
    return proc is not None and proc.poll() is None


# ---------------------------------------------------------------------------
# streams_status  (usado pela API /api/proxy/status)
# ---------------------------------------------------------------------------

async def streams_status() -> List[dict]:
    result = []
    for vid, buf in list(_buffers.items()):
        mgr  = _managers.get(vid)
        proc = _processes.get(vid)
        buffer_index = await buf.get_current_index()
        buffer_mb    = round(buffer_index * buf.chunk_size / 1024 / 1024, 2)
        result.append({
            "video_id":        vid,
            "buffer_chunks":   buffer_index,
            "buffer_index":    buffer_index,
            "buffer_mb":       buffer_mb,
            "clients":         mgr.count if mgr else 0,
            "clients_info":    mgr.snapshot() if mgr else [],
            "process_alive":   proc.poll() is None if proc else False,
            "process_pid":     proc.pid if proc else None,
            "is_placeholder":  vid in _placeholder_cmds,
        })
    return result


# ---------------------------------------------------------------------------
# get_stream_debug_info  (usado pela API /api/proxy/debug/{video_id})
# ---------------------------------------------------------------------------

async def get_stream_debug_info(video_id: str) -> Optional[dict]:
    """Retorna informações detalhadas de debug para um stream ativo.
    
    Returns:
        Dict com métricas completas ou None se stream não existe.
    """
    buf = _buffers.get(video_id)
    mgr = _managers.get(video_id)
    proc = _processes.get(video_id)
    
    if buf is None:
        return None
    
    now = time.time()
    start_time = _process_start_times.get(video_id, now)
    uptime = now - start_time
    
    # Estado do processo
    process_alive = proc.poll() is None if proc else False
    process_info = {
        "pid": proc.pid if proc else None,
        "alive": process_alive,
        "returncode": proc.returncode if proc and not process_alive else None,
        "uptime_s": round(uptime, 1),
    }
    
    # Estatísticas do buffer via Redis
    current_index = await buf.get_current_index()
    buffer_mb = current_index * buf.chunk_size / 1024 / 1024
    
    buffer_info = {
        "chunks_total": current_index,
        "buffer_mb": round(buffer_mb, 2),
        "is_active": _stream_reading.get(video_id, False),
        "chunk_size_kb": round(buf.chunk_size / 1024, 1),
        "chunk_ttl": buf.chunk_ttl,
    }
    
    # Clientes detalhados
    clients_info = mgr.debug_snapshot(current_index) if mgr else []
    
    return {
        "video_id": video_id,
        "process": process_info,
        "buffer": buffer_info,
        "clients": clients_info,
        "clients_count": mgr.count if mgr else 0,
        "is_placeholder": video_id in _placeholder_cmds,
        "debug_enabled": _debug_enabled,
    }
