"""Клиент LLM: разбор ответа, ретраи, дневной лимит вызовов (раздел 4.5 ТЗ)."""

from __future__ import annotations

import pytest

from crmai.llm import BudgetExceeded, LLMClient, LLMError, extract_json, response_text
from crmai.retry import RetryExhausted, retry_call


class Block:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class Response:
    def __init__(self, text):
        self.content = [Block(text)]


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)


class FakeAnthropic:
    def __init__(self, outcomes):
        self.messages = FakeMessages(outcomes)


def test_extract_plain_json():
    assert extract_json('{"stage": "слив"}') == {"stage": "слив"}


def test_extract_json_from_code_fence():
    assert extract_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_preamble():
    assert extract_json('Конечно! {"a": 1} — готово') == {"a": 1}


@pytest.mark.parametrize("raw", ["", "   ", "совсем не json", "[1, 2]"])
def test_broken_answers_raise(raw):
    with pytest.raises(LLMError):
        extract_json(raw)


def test_response_text_skips_non_text_blocks():
    class Mixed:
        content = [Block("", type="thinking"), Block("ответ")]

    assert response_text(Mixed()) == "ответ"


def test_complete_json_calls_model_and_counts_budget(crm_settings, crm_db):
    fake = FakeAnthropic(['{"stage": "переговоры об условиях"}'])
    llm = LLMClient(crm_settings, crm_db, client=fake)

    data = llm.complete_json("system", "user", "2026-09-21")

    assert data["stage"] == "переговоры об условиях"
    assert fake.messages.calls[0]["model"] == crm_settings.llm_model
    assert fake.messages.calls[0]["system"] == "system"
    assert crm_db.llm_calls_today("2026-09-21") == 1


def test_budget_blocks_further_calls(crm_settings, crm_db):
    crm_settings.max_llm_calls_per_day = 2
    llm = LLMClient(crm_settings, crm_db, client=FakeAnthropic(['{"a": 1}', '{"a": 2}']))

    llm.complete_json("s", "u", "2026-09-21")
    llm.complete_json("s", "u", "2026-09-21")

    assert llm.calls_left("2026-09-21") == 0
    with pytest.raises(BudgetExceeded):
        llm.complete_json("s", "u", "2026-09-21")


def test_api_error_becomes_llm_error(crm_settings, crm_db, monkeypatch):
    monkeypatch.setattr("crmai.retry.time.sleep", lambda _s: None)
    fake = FakeAnthropic([RuntimeError("503"), RuntimeError("503"), RuntimeError("503")])
    llm = LLMClient(crm_settings, crm_db, client=fake)

    with pytest.raises(LLMError):
        llm.complete_json("s", "u", "2026-09-21")
    assert len(fake.messages.calls) == 3  # три попытки по ТЗ


def test_retry_succeeds_on_second_attempt():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("сеть")
        return "ок"

    assert retry_call(flaky, sleeper=lambda _s: None) == "ок"
    assert len(attempts) == 2


def test_retry_gives_up_on_listed_errors():
    def bad_request():
        raise ValueError("400")

    with pytest.raises(ValueError):
        retry_call(bad_request, give_up_on=(ValueError,), sleeper=lambda _s: None)


def test_retry_exhausted_wraps_last_error():
    with pytest.raises(RetryExhausted):
        retry_call(lambda: (_ for _ in ()).throw(RuntimeError("нет")), sleeper=lambda _s: None)
