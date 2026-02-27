import pytest
from core.config import AppConfig, DEFAULTS
from pathlib import Path

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "test.db")

def test_total_de_chaves_retrofit(cfg):
    assert len(DEFAULTS) == 35

def test_todas_as_chaves_no_banco(cfg):
    for key in DEFAULTS:
        assert cfg.get_raw(key) is not None

def test_get_int(cfg):
    assert cfg.get_int("scheduler_main_interval_hours") == 4

def test_get_bool_true(cfg):
    assert cfg.get_bool("enable_scheduler_active_hours") is True

def test_get_bool_false(cfg):
    cfg.update("enable_scheduler_active_hours", "false")
    assert cfg.get_bool("enable_scheduler_active_hours") is False

def test_get_list(cfg):
    assert "ao vivo" in cfg.get_list("title_filter_expressions")

def test_get_mapping_sports(cfg):
    assert cfg.get_mapping("category_mappings").get("17") == "ESPORTES"

def test_update_persiste_entre_instancias(cfg, tmp_path):
    cfg.update("http_port", "9999")
    assert AppConfig(db_path=tmp_path / "test.db").get_int("http_port") == 9999

def test_chave_desconhecida_lanca_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.update("chave_inexistente", "valor")

def test_import_env_file(cfg, tmp_path):
    env = tmp_path / "test.env"
    env.write_text('YOUTUBE_API_KEY="minha_chave_teste"\n')
    cfg.import_from_env_file(env)
    assert cfg.get_str("youtube_api_key") == "minha_chave_teste"

def test_secoes_presentes(cfg):
    sections = cfg.get_all_by_section()
    for s in ("credentials", "scheduler", "filters", "output", "technical", "logging"):
        assert s in sections

def test_rows_sao_dicionarios(cfg):
    """Garante que fastlite retorna dicionários, não objetos com atributos."""
    for row in cfg._db.t.config.rows:
        assert isinstance(row, dict), f"Row deveria ser dict, é {type(row)}"
        assert "key" in row and "value" in row
