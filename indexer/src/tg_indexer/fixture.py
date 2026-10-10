"""Create a synthetic contract fixture; these vectors are NOT model embeddings."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import struct


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    schema = Path(__file__).resolve().parents[3] / "contracts/schema.sql"
    db = sqlite3.connect(output / "metadata.sqlite")
    db.executescript(schema.read_text())
    examples = [
        ("sleep", "Человек спит на диване", "спит человек диван", "alice", 1700000000),
        ("cat", "Кот смотрит в окно", "кот окно", "bob", 1710000000),
        ("work", "Мем об усталости после работы", "усталость работа мем", "alice", 1720000000),
    ]
    with (output / "vectors.bin").open("wb") as vectors:
        for index, (image, scene, lexical, sender, date) in enumerate(examples):
            db.execute("INSERT INTO images VALUES(?,?,NULL)", (image, scene))
            db.execute("INSERT INTO messages(message_key,image_id,chat_id,chat_name,sender_id,sender_name,date) VALUES(?,?,?,?,?,?,?)", (f"fixture:{index}", image, "fixture-chat", "Тестовый чат", sender, sender, date))
            db.execute("INSERT INTO representations VALUES(?,?,?,?)", (index, image, "scene", scene))
            db.execute("INSERT INTO search_text VALUES(?,?)", (image, lexical))
            vector = [0.0] * 384
            vector[index] = 1.0
            vectors.write(struct.pack("<384f", *vector))
    db.commit()
    db.close()
    files = {}
    for name in ("metadata.sqlite", "vectors.bin"):
        content = (output / name).read_bytes()
        files[name] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    manifest = {
        "format_version": 1, "dataset_id": "synthetic-fixture", "version": 1,
        "created_at": "2026-10-10T00:00:00Z", "embedding_space": "fixture-one-hot-v1",
        "model_sha256": "0" * 64, "tokenizer_sha256": "0" * 64,
        "dimension": 384, "dtype": "float32-le", "normalized": True,
        "vector_count": 3, "image_count": 3, "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
