from core.live_proxy.client_registry import ClientRegistry
from core.live_proxy.config import LiveProxyConfig
from core.live_proxy.stream_buffer import StreamBuffer


def test_stream_buffer_latest_safe_index_and_lag_clamp():
    cfg = LiveProxyConfig(buffer_maxlen=4, initial_behind_bytes=100)
    buf = StreamBuffer(video_id="v1", config=cfg)

    for _ in range(6):
        buf.add_chunk(b"x" * 50)

    # buffer_maxlen=4 keeps the last 4 chunks (global index is still monotonic)
    assert buf.index == 6
    assert buf.size == 4
    assert buf.latest_safe_index() >= 3

    # Start before buffer start must be clamped
    chunks, next_index = buf.get_optimized_client_data(0)
    assert chunks
    assert next_index >= 3


def test_stream_buffer_ready_for_clients_when_inactive():
    cfg = LiveProxyConfig(live_preroll_bytes=1024)
    buf = StreamBuffer(video_id="v2", config=cfg)
    assert not buf.ready_for_clients()
    buf.mark_inactive()
    assert buf.ready_for_clients()


def test_client_registry_late_and_snapshot():
    reg = ClientRegistry(video_id="v3")
    reg.add_client("c1", "127.0.0.1", "ua")
    assert reg.count == 1
    assert len(reg.snapshot()) == 1

    late_for = reg.mark_late("c1")
    assert late_for >= 0.0
    reg.clear_late("c1")
    reg.update_activity("c1", bytes_sent=1234, current_index=10)
    debug = reg.debug_snapshot(buffer_index=20)
    assert debug[0]["lag_chunks"] >= 0

    remaining = reg.remove_client("c1")
    assert remaining == 0
    assert reg.idle_since is not None
