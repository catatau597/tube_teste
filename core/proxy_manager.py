"""
proxy_manager.py — Gerenciamento de streams e clientes sem Redis.

Arquitetura:
  subprocess (ffmpeg/streamlink)
      |  stdout
      v
  StreamBuffer (deque circular, chunks de ~64KB)
      |  get_chunks()
      v
  N clientes via StreamingResponse (async generator)

Todo o estado fica em memória no processo FastHTML (single-process).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("TubeWrangler.proxy")

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

CHUNK_SIZE               = 65536   # 64 KB por chunk de leitura
BUFFER_MAXLEN            = 200     # máximo de chunks no deque (~12 MB)
CLIENT_TIMEOUT_S         = 30      # segundos sem receber dado → desconecta cliente
STREAM_IDLE_STOP_S       = 30      # segundos sem clientes → para o processo
INIT_TIMEOUT_S           = 15      # segundos aguardando primeiro chunk

# Duração em segundos de cada segmento do placeholder antes de encerrar naturalmente.
# Deve ser igual ao -t passado ao ffmpeg em build_ffmpeg_placeholder_cmd().
PLACEHOLDER_SEGMENT_DURATION = 30


# ---------------------------------------------------------------------------
# StreamBuffer
# ---------------------------------------------------------------------------

@dataclass
class StreamBuffer:
    """Buffer circular de chunks TS para um stream."""

    video_id: str
    chunks:   deque = field(default_factory=lambda: deque(maxlen=BUFFER_MAXLEN))
    index:    int   = 0           # índice global (monotônico)
    lock:     threading.Lock = field(default_factory=threading.Lock)
    active:   bool  = True        # False quando o processo encerrou

    def add_chunk(self, data: bytes) -> None:
        with self.lock:
            self.chunks.append(data)
            self.index += 1

    def get_chunks(self, start_index: int, count: int = 5) -> Tuple[List[bytes], int]:
        """Retorna até `count` chunks a partir de `start_index`.

        Retorna (lista_de_chunks, próximo_start_index).
        Se `start_index` ficou para trás do buffer, pula para o início disponível.
        """
        with self.lock:
            if not self.chunks:
                return [], start_index

            buffer_start = self.index - len(self.chunks)

            # cliente muito atrasado → pula para início do buffer
            if start_index < buffer_start:
                start_index = buffer_start

            offset = start_index - buffer_start
            if offset < 0 or offset >= len(self.chunks):
                return [], start_index

            chunks_list = list(self.chunks)
            end    = min(offset + count, len(chunks_list))
            result = chunks_list[offset:end]
            return result, start_index + len(result)


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
            logger.info(
                f"[{self.video_id}] cliente desconectado: {client_id}  "
                f"duração={duration:.1f}s  bytes={info.bytes_sent}  restantes={remaining}"
            )
        return remaining

    def update_activity(self, client_id: str, bytes_sent: int) -> None:
        with self._lock:
            if client_id in self._clients:
                self._clients[client_id].last_active = time.time()
                self._clients[client_id].bytes_sent  = bytes_sent

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


# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------

# video_id → objeto
_buffers:   Dict[str, StreamBuffer]  = {}
_managers:  Dict[str, ClientManager] = {}
_processes: Dict[str, subprocess.Popen] = {}

# Registra comandos de placeholder para permitir restart automático.
# video_id → List[str] cmd (somente para streams do tipo placeholder)
_placeholder_cmds: Dict[str, List[str]] = {}


# ---------------------------------------------------------------------------
# start_stream_reader
# ---------------------------------------------------------------------------

def start_stream_reader(video_id: str, cmd: List[str]) -> subprocess.Popen:
    """
    Inicia o processo (ffmpeg/streamlink) e uma thread daemon que lê
    stdout → StreamBuffer.  Também loga stderr via logging.

    Cria BufferStream e ClientManager se ainda não existirem.
    """
    if video_id not in _buffers:
        _buffers[video_id]  = StreamBuffer(video_id=video_id)
        _managers[video_id] = ClientManager(video_id=video_id)
    else:
        # Reativa o buffer para um novo segmento de placeholder
        _buffers[video_id].active = True

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    _processes[video_id] = process

    cmd_str = " ".join(cmd)
    logger.info(f"[{video_id}] processo iniciado  PID={process.pid}")
    logger.debug(f"[{video_id}] cmd completo: {cmd_str}")

    # Thread: lê stdout → buffer
    def _read_stdout() -> None:
        buf = _buffers[video_id]
        try:
            while True:
                chunk = process.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                buf.add_chunk(chunk)
        except Exception as exc:
            logger.error(f"[{video_id}] erro lendo stdout: {exc}")
        finally:
            buf.active = False
            logger.info(f"[{video_id}] stdout encerrado (index={buf.index})")

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


def restart_placeholder_if_needed(video_id: str) -> bool:
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
    start_stream_reader(video_id, cmd)
    return True


# ---------------------------------------------------------------------------
# stop_stream
# ---------------------------------------------------------------------------

def stop_stream(video_id: str) -> None:
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

    _buffers.pop(video_id, None)
    _managers.pop(video_id, None)
    logger.info(f"[{video_id}] recursos liberados")


# ---------------------------------------------------------------------------
# is_stream_active
# ---------------------------------------------------------------------------

def is_stream_active(video_id: str) -> bool:
    proc = _processes.get(video_id)
    return proc is not None and proc.poll() is None


# ---------------------------------------------------------------------------
# streams_status  (usado pela API /api/proxy/status)
# ---------------------------------------------------------------------------

def streams_status() -> List[dict]:
    result = []
    for vid, buf in list(_buffers.items()):
        mgr  = _managers.get(vid)
        proc = _processes.get(vid)
        result.append({
            "video_id":        vid,
            "buffer_chunks":   len(buf.chunks),
            "buffer_index":    buf.index,
            "buffer_mb":       round(len(buf.chunks) * CHUNK_SIZE / 1024 / 1024, 2),
            "clients":         mgr.count if mgr else 0,
            "clients_info":    mgr.snapshot() if mgr else [],
            "process_alive":   proc.poll() is None if proc else False,
            "process_pid":     proc.pid if proc else None,
            "is_placeholder":  vid in _placeholder_cmds,
        })
    return result
