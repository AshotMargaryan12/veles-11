"""Хранение ключей сервисов в .env."""

from __future__ import annotations

import os

import pytest

from tghunter.credentials import (
    FIELDS,
    SECRET_FIELDS,
    current_values,
    mask,
    masked_values,
    read_env,
    write_env,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("abcdef0123456789", "abcd…6789"),
        ("short", "s…"),
        ("x", "x"),
        ("", ""),
    ],
)
def test_mask(value, expected):
    assert mask(value) == expected


def test_mask_never_leaks_the_middle():
    secret = "AAAAsecretMIDDLEpartBBBB"
    assert "secretMIDDLE" not in mask(secret)


def test_read_env_ignores_comments_and_junk(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# комментарий\n\nTG_API_ID=123\nмусор без равно\nTGSTAT_TOKEN='в кавычках'\n",
        encoding="utf-8",
    )
    values = read_env(env)
    assert values == {"TG_API_ID": "123", "TGSTAT_TOKEN": "в кавычках"}


def test_write_env_preserves_comments_and_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# мой конфиг\nTG_API_ID=111\nEXPORT_DIR=exports\n", encoding="utf-8")

    changed = write_env({"TG_API_ID": "222", "TGSTAT_TOKEN": "tok"}, env)

    text = env.read_text(encoding="utf-8")
    assert "# мой конфиг" in text
    assert "EXPORT_DIR=exports" in text
    assert "TG_API_ID=222" in text
    assert "TGSTAT_TOKEN=tok" in text
    assert set(changed) == {"TG_API_ID", "TGSTAT_TOKEN"}


def test_write_env_creates_file_with_600(tmp_path):
    env = tmp_path / ".env"
    write_env({"TGSTAT_TOKEN": "tok"}, env)
    assert oct(os.stat(env).st_mode)[-3:] == "600"


def test_write_env_reports_only_real_changes(tmp_path):
    env = tmp_path / ".env"
    write_env({"TG_API_ID": "111"}, env)
    assert write_env({"TG_API_ID": "111"}, env) == []
    assert write_env({"TG_API_ID": "222"}, env) == ["TG_API_ID"]


def test_write_env_skips_empty_new_keys(tmp_path):
    env = tmp_path / ".env"
    write_env({"TGSTAT_TOKEN": ""}, env)
    assert "TGSTAT_TOKEN" not in read_env(env)


def test_masked_values_hide_secrets_but_not_ids(tmp_path):
    env = tmp_path / ".env"
    write_env({"TG_API_ID": "1234567", "TG_API_HASH": "abcdef0123456789"}, env)
    shown = masked_values(env)
    assert shown["TG_API_ID"] == "1234567"       # не секрет — видно целиком
    assert shown["TG_API_HASH"] == "abcd…6789"   # секрет — маской
    assert "abcdef0123456789" not in shown["TG_API_HASH"]


def test_every_secret_field_is_a_known_field():
    assert SECRET_FIELDS <= {key for key, _t, _h in FIELDS}


def test_current_values_falls_back_to_environ(tmp_path, monkeypatch):
    monkeypatch.setenv("TGSTAT_TOKEN", "из-окружения")
    assert current_values(tmp_path / "нет.env")["TGSTAT_TOKEN"] == "из-окружения"
