"""Промпты, подстановка контекста компании и формат диалога (раздел 5 ТЗ)."""

from __future__ import annotations

import pytest

from crmai.models import DIRECTION_IN, DIRECTION_OUT
from crmai.prompts import (
    Prompts,
    PromptError,
    format_dialog,
    format_prev_summary,
    format_quality_block,
    strip_comments,
    with_context,
)


class Row(dict):
    """Имитация sqlite3.Row для форматирования диалога."""


def row(direction=DIRECTION_IN, text="привет", ts="2026-09-21T09:00:00+00:00"):
    return Row(direction=direction, text=text, ts=ts)


def test_prompts_load_all_templates(crm_prompts):
    assert "ассистент руководителя отдела партнёрств" in crm_prompts.p1_system
    assert "контролёр качества" in crm_prompts.p2_system
    assert "{prev_summary}" in crm_prompts.p1_user


def test_company_context_is_appended_to_both_systems(crm_prompts):
    assert crm_prompts.company_context
    assert crm_prompts.company_context in crm_prompts.status_system()
    assert crm_prompts.company_context in crm_prompts.quality_system()
    # исходный промпт остаётся первым — контекст идёт именно в конец
    assert crm_prompts.status_system().startswith(crm_prompts.p1_system[:40])


def test_html_comments_are_not_sent_to_model(crm_prompts):
    assert "<!--" not in crm_prompts.company_context
    assert "Наполняет владелец системы" not in crm_prompts.status_system()


def test_without_context_system_prompt_unchanged():
    assert with_context("промпт", "   ") == "промпт"


def test_strip_comments_handles_multiline():
    assert strip_comments("<!--\nскрыто\n-->видно").strip() == "видно"


def test_missing_directory_raises():
    with pytest.raises(PromptError):
        Prompts.load("нет-такого-каталога")


def test_user_prompt_substitutes_values(crm_prompts):
    text = crm_prompts.status_user("прошлый статус", "[21.09 09:00] IN: привет")
    assert "прошлый статус" in text
    assert "IN: привет" in text


def test_dialog_format_roles_and_limit():
    rows = [row(text=f"m{i}") for i in range(70)]
    rows[0] = row(DIRECTION_OUT, "исходящее")
    text = format_dialog(rows)

    assert len(text.splitlines()) == 60          # последние 60 сообщений
    assert "исходящее" not in text                # самое старое отброшено
    assert text.splitlines()[0].startswith("[21.09 09:00] IN:")


def test_dialog_marks_outgoing_as_out():
    assert format_dialog([row(DIRECTION_OUT, "ответ")]).endswith("OUT: ответ")


def test_prev_summary_is_compact():
    text = format_prev_summary(
        {
            "stage": "оплачено, ждём выход",
            "temperature": "hot",
            "summary": "Ждём контент",
            "agreed": ["аванс 20.09"],
            "next_step": "пингануть",
            "next_step_date": "2026-09-25",
            "risk": "нет даты выхода",
        }
    )
    assert "оплачено, ждём выход" in text
    assert "аванс 20.09" in text
    assert "(до 2026-09-25)" in text


def test_prev_summary_empty():
    assert format_prev_summary(None) == "пусто"


def test_quality_block_has_context_and_outgoing():
    text = format_quality_block("Блогер", [row(text="вопрос")], [row(DIRECTION_OUT, "ответ")])
    assert "### Диалог: Блогер" in text
    assert "Контекст" in text and "вопрос" in text
    assert "Исходящие менеджера за день:" in text and "ответ" in text
