<!--
Шаблон черновика заявки на повышение ставки (раздел 9 ТЗ). Правится без кода.

Два вида полей:
  {{ИМЯ}}            — подставляет КОД локально: метрики, тир, CPA, структура сделки.
                       Внутренние пороги и ставки в LLM не отправляются.
  [[ИМЯ: задание]]   — текст пишет LLM по публичным данным (без LLM — простой
                       локальный текст). После двоеточия — задание для модели.

Доступные {{поля}}: PARTNER_NAME, PARTNER_TYPE, LINKS, CHECK_DATE, METRICS,
AUDIENCE_LINE, GEO_LINE, TIER_LINE, CRITERIA_LINE, CPA_LINE, DEAL_STRUCTURE,
REVIEW_TERMS, RISKS_LIST, MANAGER_NOTES,
REQUEST_LINE, VOLUME_LINE, ROI_LINE (объёмы и ROI — если указаны, иначе пусто).

Язык текста — DRAFT_LANGUAGE в .env (en по умолчанию, поддерживается ru).
Комментарии в HTML-скобках (как этот) в итоговый текст не попадают.
-->
**Subject:** Rate increase request — {{PARTNER_NAME}}

**Partner:** {{PARTNER_NAME}} ({{PARTNER_TYPE}})
**Links:** {{LINKS}}
**Checked:** {{CHECK_DATE}}, public data

**Key metrics**
{{METRICS}}

**Audience**
{{AUDIENCE_LINE}}
{{GEO_LINE}}
[[AUDIENCE_SUMMARY: 2-3 sentences on who the audience is and how likely it is to trade, grounded in the audience assessment and its evidence]]

**Requested rate and criteria**
{{REQUEST_LINE}}
{{TIER_LINE}}
{{CRITERIA_LINE}}
{{CPA_LINE}}

**Trading volume and ROI**
{{VOLUME_LINE}}
{{ROI_LINE}}

**Why this partner**
[[JUSTIFICATION: 2-4 short bullet points on why the partner is worth working with, based only on public metrics and content]]

**Weak points and risks**
[[RISKS: honest bullet points on weak points and risks, built from the risk flags and the assessment; if there are none, say what still needs to be verified]]

**Proposed deal structure**
{{DEAL_STRUCTURE}}

**Term and review**
{{REVIEW_TERMS}}
{{MANAGER_NOTES}}
