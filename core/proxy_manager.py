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
import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import islice
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("TubeWrangler.proxy")

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

CHUNK_SIZE               = 65536   # 64 KB por chunk de leitura
BUFFER_MAXLEN            = 400     # máximo de chunks no deque (~25 MB)
CLIENT_TIMEOUT_S         = 30      # segundos sem receber dado → desconecta cliente
STREAM_IDLE_STOP_S       = 30      # segundos sem clientes → para o processo
INIT_TIMEOUT_S           = 15      # segundos aguardando primeiro chunk

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
    created_at: float = field(default_factory=time.time)  # timestamp de criação
    last_chunk_at: float = field(default_factory=time.time)  # timestamp do último chunk

    def add_chunk(self, data: bytes) -> None:
        with self.lock:
            self.chunks.append(data)
            self.index += 1
            self.last_chunk_at = time.time()
            
            # Debug: log periódico de buffer state (a cada 200 chunks)
            log_every = 2000 if self.video_id in _placeholder_cmds else 200
            if _debug_enabled and self.index % log_every == 0:
                mb = len(self.chunks) * CHUNK_SIZE / 1024 / 1024
                elapsed = time.time() - self.created_at
                rate = self.index / elapsed if elapsed > 0 else 0
                logger.info(
                    f"[{self.video_id}] 📊 Buffer: index={self.index} chunks={len(self.chunks)} "
                    f"MB={mb:.2f} rate={rate:.1f}chunks/s"
                )

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
                if _debug_enabled:
                    lag = buffer_start - start_index
                    logger.warning(
                        f"[{self.video_id}] ⚠️ Cliente atrasado: pulando {lag} chunks "
                        f"(de {start_index} para {buffer_start})"
                    )
                start_index = buffer_start

            offset = start_index - buffer_start
            if offset < 0 or offset >= len(self.chunks):
                return [], start_index

            end = min(offset + count, len(self.chunks))
            # Evita copiar o deque inteiro (~25MB) a cada leitura de cliente.
            result = list(islice(self.chunks, offset, end))
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
_stream_locks: Dict[str, threading.RLock] = {}

# Registra comandos de placeholder para permitir restart automático.
# video_id → List[str] cmd (somente para streams do tipo placeholder)
_placeholder_cmds: Dict[str, List[str]] = {}


def _get_stream_lock(video_id: str) -> threading.RLock:
    lock = _stream_locks.get(video_id)
    if lock is None:
        lock = threading.RLock()
        _stream_locks[video_id] = lock
    return lock


# ---------------------------------------------------------------------------
# start_stream_reader
# ---------------------------------------------------------------------------

def start_stream_reader(video_id: str, cmd: List[str]) -> subprocess.Popen:
    """
    Inicia o processo (ffmpeg/streamlink) e uma thread daemon que lê
    stdout → StreamBuffer.  Também loga stderr via logging.

    Cria BufferStream e ClientManager se ainda não existirem.
    """
    with _get_stream_lock(video_id):
        current = _processes.get(video_id)
        if current is not None and current.poll() is None:
            logger.warning(f"[{video_id}] start ignorado: processo já ativo PID={current.pid}")
            return current

        if video_id not in _buffers:
            _buffers[video_id]  = StreamBuffer(video_id=video_id)
            _managers[video_id] = ClientManager(video_id=video_id)
        else:
            # Reativa o buffer para um novo segmento de placeholder
            _buffers[video_id].active = True
            _buffers[video_id].created_at = time.time()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        _processes[video_id] = process
        _process_start_times[video_id] = time.time()

        cmd_str = " ".join(cmd)
        logger.info(f"[{video_id}] processo iniciado  PID={process.pid}")
        logger.debug(f"[{video_id}] cmd completo: {cmd_str}")

    # Thread: lê stdout → buffer
    def _read_stdout() -> None:
        buf = _buffers[video_id]
        chunk_count = 0
        try:
            while True:
                chunk = process.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                buf.add_chunk(chunk)
                chunk_count += 1
                
                # Debug: log a cada 400 chunks lidos
                log_every = 4000 if video_id in _placeholder_cmds else 400
                if _debug_enabled and chunk_count % log_every == 0:
                    mb = chunk_count * CHUNK_SIZE / 1024 / 1024
                    logger.info(
                        f"[{video_id}] 💾 stdout thread: {chunk_count} chunks lidos "
                        f"({mb:.2f} MB)"
                    )
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
                # Linhas de progresso do ffmpeg (frame=...) são muito verbosas e
                # degradam throughput quando há vários streams/clientes.
                if "frame=" in low and "time=" in low and not _debug_enabled:
                    continue
                if any(kw in low for kw in ("error", "fail", "fatal", "invalid", "no playable", "unable")):
                    logger.warning(f"[{video_id}] stderr: {line}")
                else:
                    if _debug_enabled:
                        logger.info(f"[{video_id}] stderr: {line}")
                    else:
                        logger.debug(f"[{video_id}] stderr: {line}")
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
    with _get_stream_lock(video_id):
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
    with _get_stream_lock(video_id):
        # Remove registro de placeholder antes de qualquer outra coisa
        _placeholder_cmds.pop(video_id, None)

        proc = _processes.pop(video_id, None)
        if proc:
            try:
                if proc.poll() is None:
                    # start_new_session=True cria novo grupo; encerra grupo inteiro
                    # (evita filhos órfãos em pipelines bash -> yt-dlp | ffmpeg).
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if proc.poll() is None:
                        logger.warning(f"[{video_id}] SIGTERM timeout; enviando SIGKILL no grupo")
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
                except Exception as exc:
                    logger.warning(f"[{video_id}] erro ao forçar kill do grupo: {exc}")
            except Exception as exc:
                logger.warning(f"[{video_id}] erro ao terminar processo: {exc}")
            logger.info(f"[{video_id}] processo encerrado  PID={proc.pid} rc={proc.returncode}")

        _buffers.pop(video_id, None)
        _managers.pop(video_id, None)
        _process_start_times.pop(video_id, None)
        _stream_locks.pop(video_id, None)
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


# ---------------------------------------------------------------------------
# get_stream_debug_info  (usado pela API /api/proxy/debug/{video_id})
# ---------------------------------------------------------------------------

def get_stream_debug_info(video_id: str) -> Optional[dict]:
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
    
    # Estatísticas do buffer
    buffer_mb = len(buf.chunks) * CHUNK_SIZE / 1024 / 1024
    buffer_age = now - buf.created_at
    growth_rate = buf.index / buffer_age if buffer_age > 0 else 0
    time_since_last_chunk = now - buf.last_chunk_at
    
    buffer_info = {
        "chunks_total": buf.index,
        "chunks_in_buffer": len(buf.chunks),
        "buffer_mb": round(buffer_mb, 2),
        "buffer_age_s": round(buffer_age, 1),
        "growth_rate_chunks_per_s": round(growth_rate, 2),
        "time_since_last_chunk_s": round(time_since_last_chunk, 1),
        "is_active": buf.active,
    }
    
    # Clientes detalhados
    clients_info = mgr.debug_snapshot(buf.index) if mgr else []
    
    return {
        "video_id": video_id,
        "process": process_info,
        "buffer": buffer_info,
        "clients": clients_info,
        "clients_count": mgr.count if mgr else 0,
        "is_placeholder": video_id in _placeholder_cmds,
        "debug_enabled": _debug_enabled,
    }
