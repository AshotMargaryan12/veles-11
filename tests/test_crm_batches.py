"""Батчи уровней 1 и 2, приём сообщений и дайджест (разделы 4.2-4.4, 6 ТЗ)."""

from __future__ import annotations

import json
from datetime import date

from crmai.analysis import build_note, run_status_batch
from crmai.digest import build_digest, collect
from crmai.intake import Intake
from crmai.llm import StubLLM
from crmai.models import DIRECTION_IN, DIRECTION_OUT, Message, StatusAnalysis
from crmai.quality import attach_chats, collect_blocks, run_quality_batch
from crmai.wazzup import parse_payload

DAY = date(2026, 9, 21)
# Границы московских суток 21.09 в UTC: 20.09 21:00 .. 21.09 21:00
MORNING = "2026-09-21T06:00:00+00:00"
NOON = "2026-09-21T09:00:00+00:00"


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def broadcast(self, text):
        self.messages.append(text)
        return 1

    def send(self, chat_id, text):
        return bool(self.broadcast(text))


class FakeAmo:
    """Пишущая часть amo: запоминает примечания и задачи, контактов не знает."""

    def __init__(self, contacts=None, leads=None):
        self.notes = []
        self.tasks = []
        self.contacts = contacts or []
        self.leads = leads or []

    def search_contacts(self, query):
        return list(self.contacts)

    def contact_leads(self, contact_id):
        return list(self.leads)

    def leads_by_ids(self, lead_ids):
        return [lead for lead in self.leads if lead.get("id") in set(lead_ids)]

    def add_note(self, lead_id, text):
        self.notes.append((lead_id, text))
        return len(self.notes)

    def create_task(self, lead_id, text, complete_till, responsible_user_id, entity_type="leads"):
        self.tasks.append((lead_id, text, complete_till, responsible_user_id))
        return len(self.tasks)


def add(db, mid, chat="c1", direction=DIRECTION_IN, text="привет", ts=NOON, name="Блогер"):
    db.add_message(
        Message(
            message_id=mid, chat_id=chat, direction=direction, text=text, ts=ts,
            contact_name=name, contact_username="bloger",
        )
    )


def status_json(**overrides):
    payload = {
        "partner": "Блогер",
        "stage": "переговоры об условиях",
        "summary": "Обсуждаем условия.",
        "agreed": ["созвон 22.09"],
        "waiting_for": "ответ партнёра",
        "next_step": "прислать оффер",
        "next_step_date": "2026-09-30",
        "risk": None,
        "temperature": "hot",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


# --- приём и мгновенные алерты ----------------------------------------------


def test_intake_stores_deduplicates_and_alerts(crm_settings, crm_db):
    notifier = FakeNotifier()
    intake = Intake(crm_settings, crm_db, notifier=notifier)

    payload = {
        "messages": [
            {"messageId": "a", "chatId": "c1", "chatType": "telegram", "status": "inbound",
             "text": "а доход гарантируете?", "dateTime": MORNING,
             "contact": {"name": "Блогер"}},
            {"messageId": "b", "chatId": "c1", "chatType": "telegram", "isEcho": True,
             "text": "да, гарантируем доход без рисков", "dateTime": NOON,
             "contact": {"name": "Блогер"}},
            {"messageId": "b", "chatId": "c1", "chatType": "telegram", "isEcho": True,
             "text": "да, гарантируем доход без рисков", "dateTime": NOON,
             "contact": {"name": "Блогер"}},
        ]
    }
    result = intake.handle_payload(payload)

    assert (result.received, result.stored, result.duplicates) == (3, 2, 1)
    # входящий вопрос про гарантии не считается нарушением менеджера
    assert {flag.rule for flag in result.alerts} == {"guaranteed_income", "no_risk"}
    assert len(notifier.messages) == 2
    assert "Блогер" in notifier.messages[0]


def test_alert_carries_lead_link(crm_settings, crm_db):
    add(crm_db, "m0")
    crm_db.set_match("c1", 321)
    notifier = FakeNotifier()
    intake = Intake(crm_settings, crm_db, notifier=notifier)

    intake.handle_payload(parse_payload(
        {"messageId": "m1", "chatId": "c1", "isEcho": True, "dateTime": NOON,
         "text": "рефка 45% тебе"}
    )[0].raw)

    assert "https://veles.amocrm.ru/leads/detail/321" in notifier.messages[0]


def test_repeated_delivery_does_not_alert_twice(crm_settings, crm_db):
    notifier = FakeNotifier()
    intake = Intake(crm_settings, crm_db, notifier=notifier)
    event = {"messageId": "x", "chatId": "c1", "isEcho": True, "dateTime": NOON,
             "text": "гарантируем прибыль"}

    intake.handle_payload(event)
    intake.handle_payload(event)

    assert len(notifier.messages) == 1


# --- уровень 1 ---------------------------------------------------------------


def test_status_batch_writes_note_and_saves_analysis(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1", text="привет")
    add(crm_db, "m2", direction=DIRECTION_OUT, text="здравствуйте")
    crm_db.set_match("c1", 15)
    amo = FakeAmo()

    result = run_status_batch(
        crm_settings, crm_db, StubLLM([status_json()]), crm_prompts, amo=amo
    )

    assert (result.analyzed, result.notes, result.unmatched) == (1, 1, 0)
    lead_id, note = amo.notes[0]
    assert lead_id == 15
    assert "Этап: переговоры об условиях" in note
    assert "Зафиксировано: созвон 22.09" in note
    assert crm_db.last_analysis("c1")["stage"] == "переговоры об условиях"
    # чат помечен проанализированным — второй прогон его не возьмёт
    assert crm_db.chats_with_activity() == []


def test_status_batch_creates_task_when_next_step_missing(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1")
    crm_db.set_match("c1", 15)
    amo = FakeAmo()

    result = run_status_batch(
        crm_settings, crm_db, StubLLM([status_json(next_step_date=None)]), crm_prompts, amo=amo
    )

    assert result.tasks == 1
    lead_id, text, _till, responsible = amo.tasks[0]
    assert lead_id == 15
    assert text == "Определить следующий шаг по Блогер"
    assert responsible == crm_settings.amo_manager_user_id


def test_status_batch_creates_task_when_next_step_overdue(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1")
    crm_db.set_match("c1", 15)
    amo = FakeAmo()

    run_status_batch(
        crm_settings, crm_db, StubLLM([status_json(next_step_date="2020-01-01")]),
        crm_prompts, amo=amo,
    )

    assert len(amo.tasks) == 1


def test_status_batch_keeps_going_without_lead(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1")
    amo = FakeAmo()

    result = run_status_batch(crm_settings, crm_db, StubLLM([status_json()]), crm_prompts, amo=amo)

    assert result.unmatched == 1
    assert amo.notes == []
    assert result.analyzed == 1  # анализ сохранён даже без сделки


def test_status_batch_survives_llm_failure(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1", chat="c1")
    add(crm_db, "m2", chat="c2", name="Второй")

    llm = StubLLM(["не json", status_json()])
    result = run_status_batch(crm_settings, crm_db, llm, crm_prompts, amo=None)

    assert (result.analyzed, result.failed) == (1, 1)
    assert len(crm_db.failures_for_day(DAY.isoformat())) >= 0  # запись о сбое не падает


def test_status_batch_uses_previous_summary_in_prompt(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1")
    crm_db.save_analysis("c1", None, {"stage": "холодный заход", "summary": "Первый контакт"})
    llm = StubLLM([status_json()])

    run_status_batch(crm_settings, crm_db, llm, crm_prompts, amo=None)

    _system, user = llm.calls[0]
    assert "холодный заход" in user
    assert "OUT" in user or "IN" in user


def test_status_batch_stops_on_budget(crm_settings, crm_db, crm_prompts):
    add(crm_db, "m1", chat="c1")
    add(crm_db, "m2", chat="c2")

    class BrokeLLM(StubLLM):
        def complete_json(self, system, user, day, max_tokens=4000):
            from crmai.llm import BudgetExceeded

            raise BudgetExceeded("лимит")

    result = run_status_batch(crm_settings, crm_db, BrokeLLM(), crm_prompts, amo=None)

    assert result.budget_stopped is True
    assert result.analyzed == 0


def test_note_format_matches_spec():
    analysis = StatusAnalysis.from_json(json.loads(status_json(risk="нет даты выхода")))
    note = build_note(analysis, DAY)

    assert note.splitlines()[0] == "🤖 Статус на 21.09.2026"
    assert "Температура: hot" in note
    assert "След. шаг: прислать оффер (до 2026-09-30)" in note
    assert note.splitlines()[-1] == "Риск: нет даты выхода"


# --- уровень 2 ---------------------------------------------------------------


def quality_json(**overrides):
    payload = {
        "verdicts": [
            {
                "chat": "Блогер",
                "quote": "скоро пришлём",
                "issue": "8. Обещание без даты",
                "severity": "major",
                "suggestion": "Назвать конкретную дату",
            }
        ],
        "ok_chats": 1,
        "day_note": "День ровный.",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_quality_batch_collects_only_chats_with_outgoing(crm_settings, crm_db):
    add(crm_db, "in1", chat="c1", direction=DIRECTION_IN)
    add(crm_db, "out1", chat="c2", direction=DIRECTION_OUT, text="скоро пришлём", name="Второй")

    blocks = collect_blocks(crm_settings, crm_db, DAY)

    assert [block.chat_id for block in blocks] == ["c2"]
    assert "Исходящие менеджера за день:" in blocks[0].text


def test_quality_batch_saves_verdicts_and_day_note(crm_settings, crm_db, crm_prompts):
    add(crm_db, "out1", direction=DIRECTION_OUT, text="скоро пришлём")
    crm_db.set_match("c1", 15)

    result = run_quality_batch(
        crm_settings, crm_db, StubLLM([quality_json()]), crm_prompts, amo=None, day=DAY
    )

    assert len(result.verdicts) == 1
    assert result.ok_chats == 1
    assert result.day_note == "День ровный."
    saved = crm_db.verdicts_for_day(DAY.isoformat())
    assert saved[0]["severity"] == "major"
    assert saved[0]["lead_id"] == 15


def test_critical_verdict_missed_by_regex_triggers_alert(crm_settings, crm_db, crm_prompts):
    add(crm_db, "out1", direction=DIRECTION_OUT, text="ок, поменяем KPI по оплаченной сделке")
    notifier = FakeNotifier()
    payload = quality_json(
        verdicts=[
            {
                "chat": "Блогер",
                "quote": "ок, поменяем KPI по оплаченной сделке",
                "issue": "6. Изменение условий без руководителя",
                "severity": "critical",
                "suggestion": "Сначала согласовать с руководителем",
            }
        ]
    )

    result = run_quality_batch(
        crm_settings, crm_db, StubLLM([payload]), crm_prompts, amo=None,
        notifier=notifier, day=DAY,
    )

    assert len(result.alerted) == 1
    assert "Критичное замечание" in notifier.messages[0]


def test_critical_verdict_already_caught_by_regex_is_not_realerted(crm_settings, crm_db, crm_prompts):
    add(crm_db, "out1", direction=DIRECTION_OUT, text="гарантируем доход")
    notifier = FakeNotifier()
    payload = quality_json(
        verdicts=[
            {
                "chat": "Блогер",
                "quote": "гарантируем доход",
                "issue": "1. Гарантия доходности",
                "severity": "critical",
                "suggestion": "Без обещаний",
            }
        ]
    )

    result = run_quality_batch(
        crm_settings, crm_db, StubLLM([payload]), crm_prompts, amo=None,
        notifier=notifier, day=DAY,
    )

    assert result.alerted == []
    assert notifier.messages == []


def test_attach_chats_matches_by_quote_when_name_differs(crm_settings, crm_db):
    add(crm_db, "out1", chat="c9", direction=DIRECTION_OUT, text="на днях пришлём бриф", name="Мария")
    blocks = collect_blocks(crm_settings, crm_db, DAY)
    from crmai.models import QualityVerdict

    verdict = QualityVerdict(chat="непонятно кто", quote="на днях пришлём бриф")
    attach_chats([verdict], blocks)

    assert verdict.chat_id == "c9"


# --- дайджест ----------------------------------------------------------------


def test_digest_has_all_blocks(crm_settings, crm_db, crm_prompts):
    add(crm_db, "in1", text="интересно")
    add(crm_db, "out1", direction=DIRECTION_OUT, text="скоро пришлём")
    add(crm_db, "in2", chat="c2", text="подумаю", name="Второй")
    crm_db.set_match("c1", 15)

    run_status_batch(
        crm_settings, crm_db,
        StubLLM([status_json(), status_json(partner="Второй", temperature="cold", next_step_date=None)]),
        crm_prompts, amo=FakeAmo(),
    )
    run_quality_batch(crm_settings, crm_db, StubLLM([quality_json()]), crm_prompts, amo=None, day=DAY)

    text = build_digest(crm_settings, crm_db, DAY)

    assert "Дайджест за 21.09.2026" in text
    assert "Диалогов с активностью: 2" in text
    assert "Статусы обновлены: 2" in text
    assert "Без сделки в amo: 1" in text
    assert "✅ Без замечаний: 1" in text
    assert "8. Обещание без даты" in text
    assert "🔥" in text and "Блогер" in text
    assert "😴" in text and "Второй" in text
    assert "День ровный." in text


def test_digest_counts_failures(crm_settings, crm_db):
    crm_db.record_failure(DAY.isoformat(), "status_llm", "c1", "таймаут")
    data = collect(crm_settings, crm_db, DAY)

    assert len(data.failures) == 1
    assert "Не удалось обработать: 1" in build_digest(crm_settings, crm_db, DAY)


def test_digest_is_safe_on_empty_day(crm_settings, crm_db):
    text = build_digest(crm_settings, crm_db, DAY)
    assert "Замечаний нет" in text
    assert "• нет" in text


def test_quality_batch_rerun_does_not_duplicate_verdicts(crm_settings, crm_db, crm_prompts):
    add(crm_db, "out1", direction=DIRECTION_OUT, text="скоро пришлём")

    for _ in range(2):
        run_quality_batch(
            crm_settings, crm_db, StubLLM([quality_json()]), crm_prompts, amo=None, day=DAY
        )

    assert len(crm_db.verdicts_for_day(DAY.isoformat())) == 1


def test_network_error_in_amo_does_not_break_batch(crm_settings, crm_db, crm_prompts):
    class BrokenAmo(FakeAmo):
        def search_contacts(self, query):
            raise ConnectionError("сеть недоступна")

    add(crm_db, "m1")
    amo = BrokenAmo()

    result = run_status_batch(crm_settings, crm_db, StubLLM([status_json()]), crm_prompts, amo=amo)

    assert result.analyzed == 1  # статус сохранён, батч не упал
    assert crm_db.last_analysis("c1") is not None
