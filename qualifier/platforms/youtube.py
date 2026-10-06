"""YouTube — раздел 4.2 ТЗ.

С ключом YT_API_KEY — официальный YouTube Data API v3: точные цифры, страна и
дата создания канала. Квота: ~3 единицы на канал (channels + playlistItems +
videos) из 10 000 в сутки; результаты кэшируются на CACHE_TTL_DAYS.

Без ключа — yt-dlp по публичной странице: подписчики и просмотры округлены
(«38K»), даты публикаций приблизительные, страны и даты создания нет.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from ..config import Settings
from ..models import (
    PLATFORM_YOUTUBE,
    STATUS_ERROR,
    STATUS_NOT_FOUND,
    STATUS_UNAVAILABLE,
    ContentItem,
    PlatformData,
    PlatformRef,
)

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 20

_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


class YouTubeApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason


def parse_duration(value: Optional[str]) -> Optional[int]:
    """ISO 8601 PT1H2M3S -> секунды."""
    if not value:
        return None
    match = _DURATION_RE.fullmatch(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status(ref: PlatformRef, status: str, reason: str, source: str = "") -> PlatformData:
    return PlatformData(
        platform=PLATFORM_YOUTUBE, handle=ref.handle, url=ref.url,
        status=status, status_reason=reason, source=source,
    )


class YouTubeCollector:
    platform = PLATFORM_YOUTUBE

    def __init__(self, settings: Settings, session: Any = None, ytdlp_factory: Any = None):
        self.settings = settings
        self.session = session or requests.Session()
        self._ytdlp_factory = ytdlp_factory
        self.quota_used = 0

    def close(self) -> None:
        pass

    # --- вход --------------------------------------------------------------

    def collect(self, ref: PlatformRef) -> PlatformData:
        if self.settings.yt_api_key:
            try:
                return self._collect_api(ref)
            except YouTubeApiError as exc:
                # кончилась квота / неверный ключ — публичная страница всё ещё доступна
                if exc.status in (400, 401, 403, 429):
                    log.warning("YouTube API: %s — пробуем yt-dlp", exc)
                    data = self._collect_ytdlp(ref)
                    data.notes.append(f"YouTube API недоступен ({exc}), данные через yt-dlp")
                    return data
                return _status(ref, STATUS_ERROR, f"YouTube API: {exc}", "youtube_api")
            except requests.RequestException as exc:
                return _status(ref, STATUS_ERROR, f"YouTube API: сеть недоступна ({exc})", "youtube_api")
        return self._collect_ytdlp(ref)

    # --- YouTube Data API v3 ----------------------------------------------

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        params["key"] = self.settings.yt_api_key
        response = self.session.get(f"{API_BASE}/{path}", params=params, timeout=TIMEOUT)
        self.quota_used += 1
        if response.status_code != 200:
            reason = ""
            message = response.text[:200]
            try:
                error = response.json().get("error", {})
                message = error.get("message", message)
                reason = (error.get("errors") or [{}])[0].get("reason", "")
            except ValueError:
                pass
            raise YouTubeApiError(message, response.status_code, reason)
        return response.json()

    def _channel_lookup(self, ref: PlatformRef) -> Optional[dict[str, Any]]:
        parts = "snippet,statistics,contentDetails,brandingSettings"
        if ref.kind == "video":
            videos = self._get("videos", part="snippet", id=ref.handle).get("items") or []
            if not videos:
                return None
            channel_id = videos[0]["snippet"]["channelId"]
            items = self._get("channels", part=parts, id=channel_id).get("items") or []
        elif ref.kind == "channel_id":
            items = self._get("channels", part=parts, id=ref.handle).get("items") or []
        elif ref.kind == "user":
            items = self._get("channels", part=parts, forUsername=ref.handle).get("items") or []
        else:
            # @handle и старые кастомные ссылки: forHandle понимает оба варианта
            items = self._get("channels", part=parts, forHandle=f"@{ref.handle}").get("items") or []
        return items[0] if items else None

    def _collect_api(self, ref: PlatformRef) -> PlatformData:
        channel = self._channel_lookup(ref)
        if channel is None:
            return _status(ref, STATUS_NOT_FOUND, "канал YouTube не найден", "youtube_api")

        snippet = channel.get("snippet") or {}
        stats = channel.get("statistics") or {}
        branding = (channel.get("brandingSettings") or {}).get("channel") or {}
        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        limit = self.settings.yt_videos_limit

        items: list[ContentItem] = []
        if uploads:
            playlist = self._get(
                "playlistItems", part="contentDetails", playlistId=uploads, maxResults=limit
            ).get("items") or []
            ids = [p["contentDetails"]["videoId"] for p in playlist if p.get("contentDetails")]
            if ids:
                videos = self._get(
                    "videos", part="snippet,statistics,contentDetails", id=",".join(ids)
                ).get("items") or []
                for video in videos:
                    v_snip = video.get("snippet") or {}
                    v_stats = video.get("statistics") or {}
                    if v_snip.get("liveBroadcastContent") in ("live", "upcoming"):
                        continue
                    items.append(
                        ContentItem(
                            id=video["id"],
                            date=_parse_dt(v_snip.get("publishedAt")),
                            text=f"{v_snip.get('title', '')}\n{(v_snip.get('description') or '')[:700]}".strip(),
                            views=_int(v_stats.get("viewCount")),
                            reactions=_int(v_stats.get("likeCount")),
                            comments=_int(v_stats.get("commentCount")),
                            url=f"https://www.youtube.com/watch?v={video['id']}",
                            duration_sec=parse_duration((video.get("contentDetails") or {}).get("duration")),
                        )
                    )

        handle = snippet.get("customUrl") or ref.handle
        notes = []
        followers = None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount"))
        if followers is None:
            notes.append("автор скрыл число подписчиков")
        return PlatformData(
            platform=PLATFORM_YOUTUBE,
            handle=ref.handle,
            url=f"https://www.youtube.com/channel/{channel['id']}",
            title=snippet.get("title", ""),
            username=handle if handle.startswith("@") else f"@{handle}",
            description=snippet.get("description", "") or "",
            followers=followers,
            created_at=_parse_dt(snippet.get("publishedAt")),
            country=(snippet.get("country") or branding.get("country") or None),
            language_hint=snippet.get("defaultLanguage") or branding.get("defaultLanguage"),
            items=items,
            items_limit=limit,
            source="youtube_api",
            notes=notes,
        )

    # --- yt-dlp ------------------------------------------------------------

    def _ytdlp(self, opts: dict[str, Any]) -> Any:
        if self._ytdlp_factory is not None:
            return self._ytdlp_factory(opts)
        import yt_dlp  # отложенный импорт: пакет нужен только без YT_API_KEY

        return yt_dlp.YoutubeDL(opts)

    def _collect_ytdlp(self, ref: PlatformRef) -> PlatformData:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            if self._ytdlp_factory is None:
                return _status(
                    ref, STATUS_UNAVAILABLE,
                    "нет YT_API_KEY и не установлен yt-dlp (pip install yt-dlp)",
                    "yt_dlp",
                )

        limit = self.settings.yt_videos_limit
        base_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        url = ref.url
        try:
            if ref.kind == "video":
                with self._ytdlp(base_opts) as ydl:
                    video = ydl.extract_info(ref.url, download=False)
                url = video.get("channel_url") or video.get("uploader_url")
                if not url:
                    return _status(ref, STATUS_NOT_FOUND, "не удалось определить канал по видео", "yt_dlp")

            opts = {
                **base_opts,
                "extract_flat": "in_playlist",
                "playlistend": limit,
                # приблизительные даты публикаций без открытия каждого видео
                "extractor_args": {"youtubetab": {"approximate_date": [""]}},
            }
            with self._ytdlp(opts) as ydl:
                info = ydl.extract_info(url.rstrip("/") + "/videos", download=False)
        except Exception as exc:  # yt-dlp бросает DownloadError на всё подряд
            message = str(exc)
            if "does not exist" in message or "404" in message:
                return _status(ref, STATUS_NOT_FOUND, "канал YouTube не найден", "yt_dlp")
            return _status(ref, STATUS_ERROR, f"yt-dlp: {message[:200]}", "yt_dlp")

        items: list[ContentItem] = []
        for entry in (info.get("entries") or [])[:limit]:
            if not entry:
                continue
            ts = entry.get("timestamp")
            items.append(
                ContentItem(
                    id=str(entry.get("id")),
                    date=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                    text=(entry.get("title") or "") + ("\n" + entry["description"] if entry.get("description") else ""),
                    views=_int(entry.get("view_count")),
                    url=entry.get("url"),
                    duration_sec=_int(entry.get("duration")),
                )
            )
        uploader_id = info.get("uploader_id") or ""
        return PlatformData(
            platform=PLATFORM_YOUTUBE,
            handle=ref.handle,
            url=info.get("channel_url") or ref.url,
            title=info.get("channel") or info.get("uploader") or "",
            username=uploader_id or None,
            description=info.get("description") or "",
            followers=_int(info.get("channel_follower_count")),
            items=items,
            items_limit=limit,
            source="yt_dlp",
            approximate=True,
            notes=["без YT_API_KEY: подписчики и просмотры округлены YouTube, даты приблизительные"],
        )
