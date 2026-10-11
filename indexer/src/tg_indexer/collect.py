"""Explicit, read-only Telegram collection from chats listed in a local config."""
import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .pipeline import sha256
from .observability import LOGGER, Progress, configure_logging
from .telegram_rate import RateLimiter, client_class


def write_json(path: Path, content) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2))
    temporary.replace(path)


def scope_records(records: list, chat_ids: set, from_date=None, to_date=None) -> list:
    scoped = []
    for record in records:
        sources = [source for source in record["sources"]
                   if source["chat_id"] in chat_ids
                   and (from_date is None or source["date"] >= from_date)
                   and (to_date is None or source["date"] <= to_date)]
        if sources:
            scoped.append({**record, "sources": sources})
    return scoped


async def collect(config_path: Path, output: Path, *, requests_per_second=None) -> None:
    from telethon import types, utils
    config = json.loads(config_path.read_text())
    chats = config.get("chats", [])
    if not chats:
        raise ValueError("configure an explicit nonempty chats allowlist")
    rate = config.get("requests_per_second", 2) if requests_per_second is None else requests_per_second
    limiter = RateLimiter(rate)
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    media = output / "media"
    media.mkdir(exist_ok=True)
    session = output / "telegram"
    catalog_path = output / "catalog.json"
    records = json.loads(catalog_path.read_text()) if catalog_path.exists() else []
    images = {record["id"]: record for record in records}
    # Config controls export scope. Dropped chats remain in private working files only.
    requested_ids = set()
    known = {source["key"] for record in records for source in record["sources"]}
    limit = int(config.get("limit_per_chat", 1000))
    if not 1 <= limit <= 100_000:
        raise ValueError("limit_per_chat must be 1..100000")
    from_date = datetime.fromisoformat(config["from_date"]).replace(tzinfo=timezone.utc) if config.get("from_date") else None
    to_date = datetime.fromisoformat(config["to_date"]).replace(tzinfo=timezone.utc) if config.get("to_date") else None
    client = client_class()(str(session), api_id, api_hash, limiter=limiter, flood_sleep_threshold=0, receive_updates=False)
    progress = Progress("Сбор", len(chats) * limit)
    scanned = downloaded = skipped = 0
    stage = "авторизация / соединение"

    async def heartbeat():
        while True:
            await asyncio.sleep(10)
            progress.update(details=f"{stage}; скачано={downloaded}, пропущено={skipped}, RPC={limiter.requests}", force=True)

    LOGGER.info("Начало сбора: чатов=%d, лимит сообщений/чат=%d, RPC/с=%.2f", len(chats), limit, rate)
    LOGGER.info("Подключение к Telegram; при необходимости авторизуйтесь в локальном терминале")
    task = asyncio.create_task(heartbeat())
    try:
        # Interactive authorization happens locally; no login values are logged or exported.
        await client.start()
        LOGGER.info("Авторизация успешна; в рабочем каталоге уже %d уникальных изображений", len(records))
        account = await client.get_me()
        for chat_number, chat in enumerate(chats, 1):
            stage = f"чат {chat_number}/{len(chats)}: получение истории"
            LOGGER.info("Обработка чата %d/%d", chat_number, len(chats))
            chat_scanned = 0
            entity = await client.get_entity(chat)
            chat_id = str(utils.get_peer_id(entity))
            requested_ids.add(chat_id)
            async for message in client.iter_messages(entity, limit=limit, offset_date=to_date):
                scanned += 1
                chat_scanned += 1
                stage = f"чат {chat_number}/{len(chats)}: просмотр сообщений"
                progress.update(scanned, f"{stage}; скачано={downloaded}, пропущено={skipped}, RPC={limiter.requests}")
                if from_date and message.date < from_date:
                    break
                mime = getattr(message.file, "mime_type", "") if message.file else ""
                if not message.photo and not (message.document and mime.startswith("image/")):
                    continue
                key = f"{account.id}:{chat_id}:{message.id}"
                if key in known:
                    skipped += 1
                    continue
                stage = f"чат {chat_number}/{len(chats)}: скачивание картинки"

                def download_progress(received, total):
                    percent = int(received * 100 / total) if total else 0
                    progress.update(details=f"{stage} {percent}%; скачано={downloaded}, RPC={limiter.requests}")

                # Telethon otherwise chooses video_sizes for animated photos and saves MP4.
                photo_sizes = getattr(message.photo, "sizes", []) if message.photo else []
                thumb = max(photo_sizes, key=lambda size: getattr(size, "w", 0) * getattr(size, "h", 0)).type if photo_sizes else None
                filename = await client.download_media(message, file=str(media / f"pending-{message.id}"), progress_callback=download_progress, thumb=thumb)
                if not filename:
                    continue
                path = Path(filename)
                image_id = sha256(path)
                destination = media / (image_id + path.suffix)
                if destination.exists():
                    path.unlink()
                else:
                    path.replace(destination)
                sender = await message.get_sender()
                sender_id = str(utils.get_peer_id(sender)) if sender is not None else None
                sender_name = utils.get_display_name(sender) if sender is not None else None
                link = None
                if isinstance(entity, types.Channel):
                    link = f"https://t.me/{entity.username}/{message.id}" if entity.username else f"https://t.me/c/{entity.id}/{message.id}"
                source = {
                    "key": key, "chat_id": chat_id, "chat_name": utils.get_display_name(entity),
                    "sender_id": sender_id, "sender_name": sender_name, "username": getattr(sender, "username", None),
                    "topic_id": str(message.reply_to.reply_to_top_id) if message.reply_to and message.reply_to.reply_to_top_id else None,
                    "date": int(message.date.timestamp()), "link": link, "caption": message.message or "",
                }
                if image_id not in images:
                    record = {"id": image_id, "image": destination.relative_to(output).as_posix(), "thumbnail": destination.relative_to(output).as_posix(), "sources": []}
                    images[image_id] = record
                    records.append(record)
                images[image_id]["sources"].append(source)
                known.add(key)
                downloaded += 1
                write_json(catalog_path, records)
            LOGGER.info("Чат %d/%d завершён: просмотрено=%d", chat_number, len(chats), chat_scanned)
        scoped = scope_records(records, requested_ids, int(from_date.timestamp()) if from_date else None, int(to_date.timestamp()) if to_date else None)
        write_json(output / "selected-catalog.json", scoped)
        progress.finish(f"просмотрено={scanned}, скачано={downloaded}, ранее сохранено={skipped}, RPC={limiter.requests}")
        LOGGER.info("Готово: выбранных изображений=%d; записан selected-catalog.json", len(scoped))
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await client.disconnect()
        session_file = session.with_suffix(".session")
        if session_file.exists():
            session_file.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--requests-per-second", type=float, help="override config RPC rate (0.1..20, default 2)")
    parser.add_argument("--log-file", type=Path, help="default: OUTPUT/collect.log; rotated at 5 MiB")
    args = parser.parse_args()
    configure_logging(args.log_file or args.output / "collect.log")
    try:
        asyncio.run(collect(args.config, args.output, requests_per_second=args.requests_per_second))
    except KeyboardInterrupt:
        LOGGER.warning("Сбор остановлен пользователем. Сохранённый catalog.json остаётся; повторите запуск для продолжения")
        raise SystemExit(130) from None
    except Exception as error:
        LOGGER.error("Сбор завершился ошибкой (%s). Проверьте конфиг, окружение и доступ аккаунта к выбранным чатам", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
