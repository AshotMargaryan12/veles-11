"""Разбор входных ссылок: Telegram, YouTube, X/Twitter (раздел 3 ТЗ)."""

from __future__ import annotations

import re
import shlex
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .models import (
    PLATFORM_TELEGRAM,
    PLATFORM_X,
    PLATFORM_YOUTUBE,
    PartnerRequest,
    PlatformRef,
)


class LinkError(ValueError):
    """Ссылку не удалось разобрать — сообщение показывается менеджеру как есть."""


_TG_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_X_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_YT_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_YT_HANDLE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")

_TG_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"}

# Пути X, которые не являются профилями
_X_RESERVED = {
    "home", "i", "search", "explore", "intent", "share", "settings", "messages",
    "notifications", "hashtag", "login", "signup", "tos", "privacy",
}
_TG_RESERVED = {"joinchat", "addlist", "share", "proxy", "socks", "addstickers", "c"}


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def parse_link(raw: str) -> PlatformRef:
    """Строка менеджера -> PlatformRef. Бросает LinkError с понятным текстом."""
    text = (raw or "").strip().strip("<>").rstrip(",;")
    if not text:
        raise LinkError("пустая ссылка")

    # @username и голый username — это Telegram
    if text.startswith("@") and _TG_USERNAME.match(text[1:]):
        return _tg_ref(text[1:], raw)
    if text.lower().startswith("tg://"):
        query = parse_qs(urlparse(text).query)
        domain = (query.get("domain") or [""])[0]
        if _TG_USERNAME.match(domain):
            return _tg_ref(domain, raw)
        raise LinkError(f"не удалось разобрать tg://-ссылку: {raw}")

    url = text if re.match(r"^[a-z]+://", text, re.I) else f"https://{text}"
    host = _host(url)

    if host in _TG_HOSTS:
        return _parse_telegram(url, raw)
    if host in _YT_HOSTS:
        return _parse_youtube(url, raw)
    if host in _X_HOSTS:
        return _parse_x(url, raw)

    if "/" not in text and "." not in text and _TG_USERNAME.match(text):
        return _tg_ref(text, raw)
    raise LinkError(
        f"не распознана площадка: {raw}. Поддерживаются t.me/..., @username, "
        "youtube.com/..., x.com/..."
    )


def _tg_ref(username: str, raw: str) -> PlatformRef:
    return PlatformRef(
        platform=PLATFORM_TELEGRAM,
        handle=username,
        url=f"https://t.me/{username}",
        raw=raw,
    )


def _parse_telegram(url: str, raw: str) -> PlatformRef:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        raise LinkError(f"в ссылке нет username канала: {raw}")
    first = parts[0]
    if first.startswith("+") or first.lower() in _TG_RESERVED:
        raise LinkError(
            f"{raw}: приватная или служебная ссылка. Нужна публичная ссылка вида "
            "t.me/username — инструмент не вступает в каналы"
        )
    if first == "s" and len(parts) > 1:  # веб-превью t.me/s/username
        first = parts[1]
    if not _TG_USERNAME.match(first):
        raise LinkError(f"некорректный username Telegram в ссылке: {raw}")
    return _tg_ref(first, raw)


def _parse_youtube(url: str, raw: str) -> PlatformRef:
    parsed = urlparse(url)
    host = _host(url)
    parts = [p for p in parsed.path.split("/") if p]

    if host == "youtu.be" and parts:
        return PlatformRef(PLATFORM_YOUTUBE, parts[0], f"https://youtu.be/{parts[0]}", raw, "video")
    if parts and parts[0] in ("watch",):
        video = (parse_qs(parsed.query).get("v") or [""])[0]
        if video:
            return PlatformRef(
                PLATFORM_YOUTUBE, video, f"https://www.youtube.com/watch?v={video}", raw, "video"
            )
    if parts and parts[0] in ("shorts", "live") and len(parts) > 1:
        return PlatformRef(
            PLATFORM_YOUTUBE, parts[1], f"https://www.youtube.com/watch?v={parts[1]}", raw, "video"
        )
    if not parts:
        raise LinkError(f"в ссылке YouTube нет канала: {raw}")

    head = parts[0]
    if head.startswith("@"):
        handle = head[1:]
        return PlatformRef(PLATFORM_YOUTUBE, handle, f"https://www.youtube.com/@{handle}", raw, "handle")
    if head == "channel" and len(parts) > 1 and _YT_CHANNEL_ID.match(parts[1]):
        cid = parts[1]
        return PlatformRef(
            PLATFORM_YOUTUBE, cid, f"https://www.youtube.com/channel/{cid}", raw, "channel_id"
        )
    if head == "user" and len(parts) > 1:
        return PlatformRef(
            PLATFORM_YOUTUBE, parts[1], f"https://www.youtube.com/user/{parts[1]}", raw, "user"
        )
    if head == "c" and len(parts) > 1:
        return PlatformRef(
            PLATFORM_YOUTUBE, parts[1], f"https://www.youtube.com/c/{parts[1]}", raw, "custom"
        )
    if _YT_HANDLE.match(head) and head not in ("playlist", "results", "feed"):
        # старые кастомные ссылки youtube.com/name
        return PlatformRef(
            PLATFORM_YOUTUBE, head, f"https://www.youtube.com/{head}", raw, "custom"
        )
    raise LinkError(f"не удалось разобрать ссылку YouTube: {raw}")


def _parse_x(url: str, raw: str) -> PlatformRef:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0].lower() in _X_RESERVED:
        raise LinkError(f"в ссылке X нет профиля: {raw}")
    handle = parts[0].lstrip("@")
    if not _X_USERNAME.match(handle):
        raise LinkError(f"некорректный username X в ссылке: {raw}")
    return PlatformRef(PLATFORM_X, handle, f"https://x.com/{handle}", raw)


def parse_links(raw_links: list[str]) -> list[PlatformRef]:
    """Несколько ссылок одного партнёра; дубли схлопываются."""
    refs: list[PlatformRef] = []
    seen: set[str] = set()
    for raw in raw_links:
        for piece in re.split(r"[\s,]+", raw.strip()):
            if not piece:
                continue
            ref = parse_link(piece)
            if ref.key in seen:
                continue
            seen.add(ref.key)
            refs.append(ref)
    if not refs:
        raise LinkError("не передано ни одной ссылки")
    return refs


_OPTION_KEYS = {"geo", "type", "notes"}


def parse_batch_line(line: str, defaults: Optional[PartnerRequest] = None) -> Optional[PartnerRequest]:
    """Строка файла пакетного режима -> PartnerRequest.

    Формат: ссылки через пробел или запятую, опционально geo=EG type=institutional
    notes="..." . Пустые строки и строки с # пропускаются.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    try:
        tokens = shlex.split(stripped, comments=False)
    except ValueError as exc:
        raise LinkError(f"не удалось разобрать строку: {line!r} ({exc})") from exc

    links: list[str] = []
    options: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep and key.lower() in _OPTION_KEYS:
            options[key.lower()] = value
        else:
            links.extend(p for p in token.split(",") if p)

    base = defaults or PartnerRequest(links=[])
    return PartnerRequest(
        links=links,
        geo=(options.get("geo") or base.geo or None),
        affiliate_type=options.get("type") or base.affiliate_type,
        notes=options.get("notes") or base.notes,
        refresh=base.refresh,
    )
