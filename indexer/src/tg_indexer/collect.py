"""Explicit, read-only Telegram collection from chats listed in a local config."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .pipeline import sha256


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


async def collect(config_path: Path, output: Path) -> None:
    from telethon import TelegramClient, errors, types, utils
    config = json.loads(config_path.read_text())
    chats = config.get("chats", [])
    if not chats:
        raise ValueError("configure an explicit nonempty chats allowlist")
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
    client = TelegramClient(str(session), api_id, api_hash, flood_sleep_threshold=60)
    # Interactive authorization happens locally; no login values are logged or exported.
    await client.start()
    try:
        account = await client.get_me()
        for chat in chats:
            entity = await client.get_entity(chat)
            chat_id = str(utils.get_peer_id(entity))
            requested_ids.add(chat_id)
            async for message in client.iter_messages(entity, limit=limit, offset_date=to_date):
                if from_date and message.date < from_date:
                    break
                mime = getattr(message.file, "mime_type", "") if message.file else ""
                if not message.photo and not (message.document and mime.startswith("image/")):
                    continue
                key = f"{account.id}:{chat_id}:{message.id}"
                if key in known:
                    continue
                while True:
                    try:
                        filename = await client.download_media(message, file=str(media / f"pending-{message.id}"))
                        break
                    except errors.FloodWaitError as error:
                        await asyncio.sleep(error.seconds)
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
                write_json(catalog_path, records)
        scoped = scope_records(records, requested_ids, int(from_date.timestamp()) if from_date else None, int(to_date.timestamp()) if to_date else None)
        write_json(output / "selected-catalog.json", scoped)
    finally:
        await client.disconnect()
        session_file = session.with_suffix(".session")
        if session_file.exists():
            session_file.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(collect(args.config, args.output))


if __name__ == "__main__":
    main()
