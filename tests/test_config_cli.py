"""Конфигурация (раздел 10) и CLI (раздел 11)."""

from __future__ import annotations

import pytest

from tghunter.cli import build_parser, main
from tghunter.config import Settings, load_blacklist, load_streams, read_list_file


def test_all_spec_presets_exist(streams):
    for name in ("neighbors", "crypto_core", "invest_tradfi", "fin_literacy",
                 "signals", "regional"):
        assert name in streams


def test_defaults_are_inherited_by_presets(streams):
    assert streams["crypto_core"].min_er == 3.0
    assert streams["crypto_core"].max_days_since_post == 14
    assert streams["crypto_core"].min_posts_30d == 4


def test_preset_overrides_win(streams):
    assert streams["fin_literacy"].max_subscribers == 15000  # нано-конвейер 1-15K
    assert streams["crypto_core"].min_subscribers == 3000    # основной 3-50K


def test_neighbors_is_similar_only(streams):
    neighbors = streams["neighbors"]
    assert neighbors.keywords == []
    assert "keywords" not in neighbors.methods
    assert "similar" in neighbors.methods


def test_signals_has_extra_blacklist_and_separate_export(streams):
    signals = streams["signals"]
    assert signals.separate_export is True
    assert signals.extra_blacklist


def test_regional_is_longlist_only(streams):
    regional = streams["regional"]
    assert regional.longlist_mode is True
    assert "turkic" in regional.languages


def test_extra_blacklist_extends_base_list(streams):
    base = load_blacklist("config")
    extended = load_blacklist("config", streams["signals"].extra_blacklist)
    assert set(base).issubset(set(extended))
    assert len(extended) > len(base)


def test_blacklist_contains_spec_markers():
    markers = load_blacklist("config")
    for required in ("казино", "casino", "ставк", "бесплатные сигналы", "памп",
                     "pump", "x100", "раздача", "airdrop"):
        assert required in markers


def test_read_list_file_ignores_comments(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# комментарий\n\n@one\n  @two  \n", encoding="utf-8")
    assert read_list_file(path) == ["@one", "@two"]


def test_read_list_file_missing_returns_empty(tmp_path):
    assert read_list_file(tmp_path / "nope.txt") == []


def test_unknown_preset_field_is_rejected(tmp_path):
    path = tmp_path / "streams.yaml"
    path.write_text(
        "defaults: {}\nstreams:\n  - name: broken\n    opechatka: 5\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Неизвестные поля"):
        load_streams(path)


def test_duplicate_preset_is_rejected(tmp_path):
    path = tmp_path / "streams.yaml"
    path.write_text(
        "defaults: {}\nstreams:\n  - name: dup\n  - name: dup\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Дубль пресета"):
        load_streams(path)


def test_settings_optional_integrations_are_off_by_default():
    settings = Settings()
    assert settings.summary_enabled is False
    assert settings.amo_enabled is False
    assert settings.sheets_enabled is False


def test_settings_session_path_is_inside_session_dir():
    settings = Settings(session_dir="sessions", session_name="hunter")
    assert settings.session_path == "sessions/hunter"


# --- CLI -------------------------------------------------------------------

def test_cli_exposes_all_spec_commands():
    parser = build_parser()
    for command in ("run", "enrich", "import-existing", "export", "stats"):
        assert parser.parse_args([command, *_args_for(command)]).command == command


def _args_for(command):
    return {
        "run": ["--stream", "crypto_core"],
        "enrich": ["file.csv"],
        "import-existing": ["file.csv"],
        "export": [],
        "stats": [],
    }[command]


def test_run_requires_stream_or_all():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run"])


def test_run_stream_and_all_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--all", "--stream", "x"])


def test_export_flags_parsed():
    args = build_parser().parse_args(
        ["export", "--stream", "crypto_core", "--new-only", "--min-score", "50"]
    )
    assert (args.stream, args.new_only, args.min_score) == ("crypto_core", True, 50)


def test_import_existing_end_to_end(tmp_path, capsys, monkeypatch):
    source = tmp_path / "crm.csv"
    source.write_text("username\n@from_crm\nhttps://t.me/second\n", encoding="utf-8")
    db_path = tmp_path / "test.db"

    code = main(["--db", str(db_path), "import-existing", str(source)])
    assert code == 0
    assert "Импортировано 2" in capsys.readouterr().out

    from tghunter.db import Database

    with Database(db_path) as db:
        assert "from_crm" in db.known_keys()


def test_stats_end_to_end(tmp_path, capsys):
    db_path = tmp_path / "stats.db"
    from tghunter.db import Database

    with Database(db_path) as db:
        db.upsert_channel(
            {"channel_id": 1, "username": "a", "subscribers": 5000, "contact": "@x",
             "method": "similar", "link": "https://t.me/a"},
            75, "similar+25", "crypto_core", True, [],
        )

    assert main(["--db", str(db_path), "stats"]) == 0
    out = capsys.readouterr().out
    assert "Всего каналов в базе: 1" in out
    assert "crypto_core" in out


def test_export_end_to_end(tmp_path, capsys):
    db_path = tmp_path / "exp.db"
    from tghunter.db import Database

    with Database(db_path) as db:
        db.upsert_channel(
            {"channel_id": 1, "username": "top", "subscribers": 9000, "er": 9.0,
             "contact": "@adv", "method": "similar", "link": "https://t.me/top"},
            95, "similar+25", "crypto_core", True, [],
        )
        db.upsert_channel(
            {"channel_id": 2, "username": "low", "subscribers": 2000, "er": 4.0,
             "contact": "@adv", "method": "keywords", "link": "https://t.me/low"},
            20, "", "crypto_core", True, [],
        )

    out_file = tmp_path / "out.csv"
    code = main(["--db", str(db_path), "export", "--stream", "crypto_core",
                 "--min-score", "50", "--out", str(out_file)])
    assert code == 0
    assert out_file.exists()

    import csv

    with out_file.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert [r["username"] for r in rows] == ["top"]


def test_export_with_nothing_to_export(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "empty.db"), "export"])
    assert code == 0
    assert "Нечего выгружать" in capsys.readouterr().out


def test_unknown_stream_is_reported(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "x.db"), "run", "--stream", "nonexistent"])
    assert code == 1
    assert "не найден" in capsys.readouterr().err
