"""
core/stream_buffer.py
---------------------
Buffer management for streaming with Redis persistence.
Inspired by Dispatcharr's stream_buffer.py architecture.
"""

import asyncio
import logging
import time
from typing import Optional, List
import redis.asyncio as redis

logger = logging.getLogger("TubeWrangler.proxy")


class StreamBuffer:
    """Manages stream buffering with Redis persistence"""
    
    def __init__(self, video_id: str, redis_url: str, config: dict):
        self.video_id = video_id
        self.redis_client = redis.from_url(redis_url, decode_responses=False)
        
        # Configurações
        self.chunk_size = config.get("buffer_chunk_size_kb", 1024) * 1024  # KB → bytes
        self.chunk_ttl = config.get("buffer_chunk_ttl", 90)
        self.max_buffer_mb = config.get("buffer_max_memory_mb", 50)
        
        # Buffers
        self._write_buffer = bytearray()
        
        # Índices
        self.head_index = 0  # Último chunk gravado no Redis
        
        # Redis keys
        self.buffer_index_key = f"proxy:{video_id}:buffer_index"
        self.buffer_chunk_prefix = f"proxy:{video_id}:chunk:"
        
        logger.info(
            f"{video_id} StreamBuffer inicializado - "
            f"chunk_size={self.chunk_size/1024:.0f}KB, ttl={self.chunk_ttl}s"
        )
    
    async def initialize(self):
        """Inicializa buffer do Redis (se já existir)"""
        try:
            current_index = await self.redis_client.get(self.buffer_index_key)
            if current_index:
                self.head_index = int(current_index)
                logger.info(f"{self.video_id} Recuperado índice do Redis: {self.head_index}")
        except Exception as e:
            logger.error(f"{self.video_id} Erro inicializando buffer: {e}")
    
    async def add_data(self, data: bytes) -> bool:
        """Adiciona dados ao buffer e grava no Redis quando atingir chunk_size"""
        if not data:
            return False
        
        try:
            # Acumula no buffer temporário
            self._write_buffer.extend(data)
            
            # Grava chunks completos no Redis
            writes_done = 0
            while len(self._write_buffer) >= self.chunk_size:
                chunk_data = bytes(self._write_buffer[:self.chunk_size])
                self._write_buffer = self._write_buffer[self.chunk_size:]
                
                # Incrementa índice e grava no Redis
                self.head_index = await self.redis_client.incr(self.buffer_index_key)
                chunk_key = f"{self.buffer_chunk_prefix}{self.head_index}"
                
                await self.redis_client.setex(chunk_key, self.chunk_ttl, chunk_data)
                writes_done += 1
            
            if writes_done > 0:
                chunk_mb = (self.chunk_size / 1024 / 1024)
                logger.debug(
                    f"{self.video_id} Gravados {writes_done} chunks ({chunk_mb:.2f}MB cada) "
                    f"no Redis, index={self.head_index}"
                )
            
            return True
        
        except Exception as e:
            logger.error(f"{self.video_id} Erro adicionando dados ao buffer: {e}")
            return False
    
    async def get_chunk(self, index: int) -> Optional[bytes]:
        """Busca um chunk específico do Redis"""
        try:
            chunk_key = f"{self.buffer_chunk_prefix}{index}"
            chunk = await self.redis_client.get(chunk_key)
            
            if chunk is None:
                logger.debug(
                    f"{self.video_id} Chunk {index} não encontrado no Redis "
                    f"(expirado ou não existe)"
                )
            
            return chunk
        
        except Exception as e:
            logger.error(f"{self.video_id} Erro buscando chunk {index}: {e}")
            return None
    
    async def get_current_index(self) -> int:
        """Retorna o índice atual do buffer"""
        try:
            current = await self.redis_client.get(self.buffer_index_key)
            return int(current) if current else 0
        except Exception:
            return self.head_index
    
    async def flush(self):
        """Grava dados restantes no buffer como chunk final"""
        if len(self._write_buffer) > 0:
            try:
                final_chunk = bytes(self._write_buffer)
                self.head_index = await self.redis_client.incr(self.buffer_index_key)
                chunk_key = f"{self.buffer_chunk_prefix}{self.head_index}"
                
                await self.redis_client.setex(chunk_key, self.chunk_ttl, final_chunk)
                logger.info(
                    f"{self.video_id} Flush final: {len(final_chunk)} bytes, "
                    f"index={self.head_index}"
                )
                
                self._write_buffer = bytearray()
            except Exception as e:
                logger.error(f"{self.video_id} Erro no flush final: {e}")
    
    async def cleanup(self):
        """Limpa recursos e fecha conexão Redis"""
        await self.flush()
        await self.redis_client.close()
        logger.info(f"{self.video_id} StreamBuffer encerrado")
