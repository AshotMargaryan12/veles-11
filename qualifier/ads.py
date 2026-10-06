"""Признаки продажи рекламы и реферальной активности в постах (раздел 4.1 ТЗ).

Пост считается рекламным, если в нём есть маркировка (#реклама, erid, #промо,
«на правах рекламы» и т. п.) или ссылка с utm / реферальными параметрами.
Просто ссылка на биржу рекламой не считается: каналы про крипту ссылаются на
биржи постоянно, без оплаты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from tghunter.textscan import extract_contact, extract_urls

# Маркировка рекламы в тексте
_TEXT_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("#реклама", r"#\s?реклама"),
        ("#промо", r"#\s?промо"),
        ("#ad", r"#(ad|ads|adv|advert|advertising)\b"),
        ("#sponsored", r"#sponsored\b"),
        ("#партнёр", r"#партн[её]р"),
        ("erid", r"\berid\b"),
        ("реклама.", r"(?:^|\n)\s*реклама[.:]"),
        ("на правах рекламы", r"на правах рекламы"),
        ("рекламный пост", r"рекламн(?:ый|ая) (?:пост|запись|интеграция)"),
        ("партнёрский пост", r"партн[её]рск(?:ий|ая) (?:пост|материал|публикация)"),
        ("sponsored", r"\b(?:sponsored by|paid partnership|paid promotion)\b"),
        ("#إعلان", r"#\s?إعلان"),
        ("إعلان مدفوع", r"إعلان مدفوع"),
        ("#reklam", r"#reklam\b"),
        ("#publicidad", r"#publicidad\b"),
    )
)

# Параметры ссылок: utm-разметка и реферальные коды
_UTM_PREFIX = "utm_"
_REF_PARAMS = {
    "ref", "ref_id", "refid", "ref_code", "refcode", "referral", "referral_code",
    "referralcode", "invite", "invite_code", "invitecode", "inviter", "aff", "aff_id",
    "affiliate", "affiliate_id", "partner", "partner_id", "promo", "promocode",
    "clickid", "click_id", "sub_id", "subid", "code", "r",
}
# Пути реферальных ссылок бирж: /register?ref=..., /invite/XXXX, /join/XXXX
_REF_PATH = re.compile(r"/(?:invite|referral|ref|join|r|b)/[A-Za-z0-9_-]{3,}", re.IGNORECASE)
# партнёрские поддомены бирж: partner.bybit.com/b/<код>, affiliate.<биржа>...
_REF_HOSTS = ("partner.", "partners.", "affiliate.", "affiliates.", "ref.", "invite.")

# Биржи и сервисы: реферальная ссылка на них — «реферальная активность на биржи»
EXCHANGE_DOMAINS = (
    "binance.", "bybit.", "okx.", "bitget.", "mexc.", "bingx.", "kucoin.", "gate.io",
    "gate.com", "htx.", "huobi.", "bitmart.", "weex.", "lbank.", "xt.com", "coinex.",
    "phemex.", "blofin.", "toobit.", "bitunix.", "bitfinex.", "kraken.", "coinbase.",
    "whitebit.", "exmo.", "bitpanda.",
)


@dataclass
class AdScan:
    is_ad: bool = False
    markers: list[str] = field(default_factory=list)
    exchange_ref: bool = False


def _url_markers(url: str) -> tuple[list[str], bool]:
    markers: list[str] = []
    try:
        parsed = urlparse(url)
    except ValueError:
        return markers, False
    host = (parsed.hostname or "").lower()
    params = {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if any(p.startswith(_UTM_PREFIX) for p in params):
        markers.append("utm")
    ref_hits = params & _REF_PARAMS
    # «code» и «r» слишком общие: считаем их реферальными только у бирж
    is_exchange = any(domain in host for domain in EXCHANGE_DOMAINS)
    if not is_exchange:
        ref_hits -= {"code", "r"}
    if ref_hits:
        markers.append("ref-ссылка")
    elif is_exchange and (_REF_PATH.search(parsed.path or "") or host.startswith(_REF_HOSTS)):
        markers.append("ref-ссылка")
    exchange_ref = is_exchange and "ref-ссылка" in markers
    return markers, exchange_ref


def scan_post(text: str, urls: Optional[list[str]] = None) -> AdScan:
    """Рекламные маркеры одного поста."""
    if not text and not urls:
        return AdScan()
    found: list[str] = []
    for name, pattern in _TEXT_MARKERS:
        if text and pattern.search(text):
            found.append(name)
    exchange_ref = False
    for url in urls if urls is not None else extract_urls(text or ""):
        url_markers, ex_ref = _url_markers(url)
        exchange_ref = exchange_ref or ex_ref
        for marker in url_markers:
            if marker not in found:
                found.append(marker)
    return AdScan(is_ad=bool(found), markers=found, exchange_ref=exchange_ref)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TME_CONTACT_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})\b", re.IGNORECASE)


def find_contact(description: str, own_handle: Optional[str] = None) -> Optional[str]:
    """Контакт для связи из описания: @username рядом с «по рекламе», t.me, e-mail."""
    if not description:
        return None
    # без e-mail: иначе «team@example.com» превращается в контакт «@example»
    contact = extract_contact(_EMAIL_RE.sub(" ", description))
    own = (own_handle or "").lstrip("@").lower()
    if contact and contact.lstrip("@").lower() != own:
        return contact
    for match in _TME_CONTACT_RE.findall(description):
        if match.lower() != own:
            return f"@{match}"
    email = _EMAIL_RE.search(description)
    if email:
        return email.group(0)
    return None
