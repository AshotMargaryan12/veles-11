"""Источники данных о каналах.

Все источники реализуют один и тот же интерфейс шлюза, что и `tghunter.tg`:

    search_keyword(keyword, limit)  -> list[Candidate]
    similar_channels(username, limit) -> list[Candidate]
    channel_mentions(username, posts_limit) -> list[Candidate]
    folder_channels(url) -> list[Candidate]
    fetch_snapshot(username, ...) -> ChannelSnapshot | None
    disconnect() -> None

Благодаря этому любой источник подставляется в конвейер без правок остального
кода: `--source telegram` (MTProto), `--source tgstat` (API-ключ), `--source demo`.
"""

from .registry import SOURCES, build_source, describe_sources, source_status

__all__ = ["SOURCES", "build_source", "describe_sources", "source_status"]
