"""
tests/test_player_router.py
Testes unitarios para core/player_router.py.

Cobre:
- _escape_ffmpeg_text: caracteres especiais
- build_streamlink_cmd: estrutura do comando
- build_vod_cmd: modo CDN e modo fallback
- build_ffmpeg_placeholder_cmd: com e sem fonte valida
- build_player_command: despacho por status
- build_player_command_async: despacho por status (mock de resolve_vod_url_async)
- resolve_vod_url_async: timeout e falha (mock de subprocess)
"""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.player_router import (
    _escape_ffmpeg_text,
    _get_texts_from_cache,
    build_ffmpeg_placeholder_cmd,
    build_player_command,
    build_player_command_async,
    build_streamlink_cmd,
    build_vod_cmd,
    resolve_vod_url_async,
)


# ---------------------------------------------------------------------------
# _escape_ffmpeg_text
# ---------------------------------------------------------------------------

def test_escape_ffmpeg_text_barra_invertida():
    assert _escape_ffmpeg_text("\\") == "\\\\"


def test_escape_ffmpeg_text_aspas_simples():
    assert "\'" in _escape_ffmpeg_text("it's")


def test_escape_ffmpeg_text_dois_pontos():
    assert "\\:" in _escape_ffmpeg_text("hora: 10")


def test_escape_ffmpeg_text_porcentagem():
    assert "\\%" in _escape_ffmpeg_text("50% off")


def test_escape_ffmpeg_text_virgula():
    assert "\\," in _escape_ffmpeg_text("a, b")


def test_escape_ffmpeg_text_string_limpa():
    assert _escape_ffmpeg_text("TubeWrangler") == "TubeWrangler"


def test_escape_ffmpeg_text_ordem_barras_primeiro():
    # Se barras nao forem escapadas primeiro, o restante seria double-escapado
    resultado = _escape_ffmpeg_text("C:\\path: 50%")
    assert resultado.startswith("C:\\\\path")


# ---------------------------------------------------------------------------
# build_streamlink_cmd
# ---------------------------------------------------------------------------

def test_streamlink_cmd_contem_best():
    cmd = build_streamlink_cmd("https://youtube.com/watch?v=abc")
    assert "best" in cmd


def test_streamlink_cmd_contem_stdout():
    cmd = build_streamlink_cmd("https://youtube.com/watch?v=abc")
    assert "--stdout" in cmd


def test_streamlink_cmd_contem_url():
    url = "https://youtube.com/watch?v=XYZ123"
    cmd = build_streamlink_cmd(url)
    assert url in cmd


def test_streamlink_cmd_user_agent_customizado():
    cmd = build_streamlink_cmd("https://youtube.com/watch?v=abc", user_agent="TestAgent/1.0")
    assert any("TestAgent/1.0" in arg for arg in cmd)


# ---------------------------------------------------------------------------
# build_vod_cmd
# ---------------------------------------------------------------------------

def test_vod_cmd_com_cdn_url_usa_ffmpeg():
    cmd = build_vod_cmd(
        cdn_url="https://cdn.example.com/video.m4v",
        watch_url="https://youtube.com/watch?v=abc",
    )
    assert cmd[0] == "ffmpeg"
    assert "https://cdn.example.com/video.m4v" in cmd


def test_vod_cmd_com_cdn_url_mpegts():
    cmd = build_vod_cmd(cdn_url="https://cdn.example.com/video.m4v", watch_url="https://youtube.com/watch?v=abc")
    assert "-f" in cmd
    assert "mpegts" in cmd


def test_vod_cmd_sem_cdn_url_usa_bash_fallback():
    cmd = build_vod_cmd(cdn_url="", watch_url="https://youtube.com/watch?v=abc")
    assert cmd[0] == "bash"
    assert "-lc" in cmd
    assert "yt-dlp" in cmd[2]
    assert "ffmpeg" in cmd[2]


def test_vod_cmd_fallback_contem_pipefail():
    cmd = build_vod_cmd(cdn_url="", watch_url="https://youtube.com/watch?v=abc")
    assert "set -o pipefail" in cmd[2]


# ---------------------------------------------------------------------------
# build_ffmpeg_placeholder_cmd
# ---------------------------------------------------------------------------

def test_placeholder_cmd_sem_fonte_sem_drawtext(tmp_path):
    """Com fonte invalida nao deve criar drawtext nem temp_files."""
    cmd, temp_files = build_ffmpeg_placeholder_cmd(
        image_url="https://example.com/thumb.jpg",
        text_line1="Linha 1",
        font_path=str(tmp_path / "nao_existe.ttf"),
    )
    assert cmd[0] == "ffmpeg"
    assert temp_files == []
    assert not any("drawtext" in arg for arg in cmd)


def test_placeholder_cmd_com_fonte_valida_cria_temp_files(tmp_path):
    """Com fonte valida e texto deve criar arquivos temporarios."""
    font = tmp_path / "fake.ttf"
    font.write_bytes(b"fake font")
    cmd, temp_files = build_ffmpeg_placeholder_cmd(
        image_url="https://example.com/thumb.jpg",
        text_line1="Titulo do Evento",
        font_path=str(font),
    )
    assert len(temp_files) == 1
    assert Path(temp_files[0]).exists()
    assert any("drawtext" in arg for arg in cmd)
    # Limpar
    for tf in temp_files:
        Path(tf).unlink(missing_ok=True)


def test_placeholder_cmd_duas_linhas_dois_temp_files(tmp_path):
    font = tmp_path / "fake.ttf"
    font.write_bytes(b"fake font")
    cmd, temp_files = build_ffmpeg_placeholder_cmd(
        image_url="/local/thumb.jpg",
        text_line1="Linha 1",
        text_line2="Linha 2",
        font_path=str(font),
    )
    assert len(temp_files) == 2
    for tf in temp_files:
        Path(tf).unlink(missing_ok=True)


def test_placeholder_cmd_image_local_sem_headers():
    cmd, _ = build_ffmpeg_placeholder_cmd(
        image_url="/data/thumb.jpg",
        font_path="/nao/existe.ttf",
    )
    assert "-headers" not in cmd
    assert "/data/thumb.jpg" in cmd


def test_placeholder_cmd_image_remota_com_headers():
    cmd, _ = build_ffmpeg_placeholder_cmd(
        image_url="https://example.com/thumb.jpg",
        font_path="/nao/existe.ttf",
    )
    assert "-headers" in cmd


# ---------------------------------------------------------------------------
# build_player_command (sincrono)
# ---------------------------------------------------------------------------

def test_build_player_command_live_usa_streamlink():
    cmd, temp_files = build_player_command(
        video_id="abc",
        status="live",
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
    )
    assert cmd[0] == "streamlink"
    assert temp_files == []


def test_build_player_command_none_usa_fallback():
    """Chamada sincrona com status=none deve retornar fallback bash (sem CDN)."""
    cmd, temp_files = build_player_command(
        video_id="abc",
        status="none",
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
    )
    assert cmd[0] == "bash"
    assert temp_files == []


def test_build_player_command_upcoming_usa_ffmpeg_placeholder():
    cmd, _ = build_player_command(
        video_id="abc",
        status="upcoming",
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
        font_path="/nao/existe.ttf",
    )
    assert cmd[0] == "ffmpeg"


def test_build_player_command_status_none_usa_placeholder():
    """Status None (Python None) deve usar placeholder, nao streamlink nem bash."""
    cmd, _ = build_player_command(
        video_id="abc",
        status=None,
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
        font_path="/nao/existe.ttf",
    )
    assert cmd[0] == "ffmpeg"


# ---------------------------------------------------------------------------
# build_player_command_async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_player_command_async_live_usa_streamlink():
    cmd, temp_files = await build_player_command_async(
        video_id="abc",
        status="live",
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
    )
    assert cmd[0] == "streamlink"
    assert temp_files == []


@pytest.mark.asyncio
async def test_build_player_command_async_none_com_cdn_usa_ffmpeg():
    """status=none com CDN resolvida deve retornar ffmpeg direto na CDN."""
    with patch(
        "core.player_router.resolve_vod_url_async",
        new_callable=AsyncMock,
        return_value="https://cdn.example.com/video.m4v",
    ):
        cmd, temp_files = await build_player_command_async(
            video_id="abc",
            status="none",
            watch_url="https://youtube.com/watch?v=abc",
            thumbnail_url="https://example.com/thumb.jpg",
        )
    assert cmd[0] == "ffmpeg"
    assert "https://cdn.example.com/video.m4v" in cmd
    assert temp_files == []


@pytest.mark.asyncio
async def test_build_player_command_async_none_sem_cdn_usa_fallback():
    """status=none com CDN vazia deve retornar fallback bash."""
    with patch(
        "core.player_router.resolve_vod_url_async",
        new_callable=AsyncMock,
        return_value="",
    ):
        cmd, temp_files = await build_player_command_async(
            video_id="abc",
            status="none",
            watch_url="https://youtube.com/watch?v=abc",
            thumbnail_url="https://example.com/thumb.jpg",
        )
    assert cmd[0] == "bash"
    assert temp_files == []


@pytest.mark.asyncio
async def test_build_player_command_async_upcoming_usa_placeholder():
    cmd, _ = await build_player_command_async(
        video_id="abc",
        status="upcoming",
        watch_url="https://youtube.com/watch?v=abc",
        thumbnail_url="https://example.com/thumb.jpg",
        font_path="/nao/existe.ttf",
    )
    assert cmd[0] == "ffmpeg"


# ---------------------------------------------------------------------------
# resolve_vod_url_async (mock de subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_vod_url_async_retorna_url_em_sucesso():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"https://cdn.example.com/video.m4v\n", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await resolve_vod_url_async("https://youtube.com/watch?v=abc")

    assert result == "https://cdn.example.com/video.m4v"


@pytest.mark.asyncio
async def test_resolve_vod_url_async_retorna_vazio_em_erro_rc():
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"erro qualquer"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await resolve_vod_url_async("https://youtube.com/watch?v=abc")

    assert result == ""


@pytest.mark.asyncio
async def test_resolve_vod_url_async_retorna_vazio_em_timeout():
    mock_proc = AsyncMock()
    mock_proc.returncode = None
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await resolve_vod_url_async("https://youtube.com/watch?v=abc", timeout=1)

    assert result == ""


@pytest.mark.asyncio
async def test_resolve_vod_url_async_retorna_vazio_em_excecao():
    with patch("asyncio.create_subprocess_exec", side_effect=OSError("bin not found")):
        result = await resolve_vod_url_async("https://youtube.com/watch?v=abc")

    assert result == ""
