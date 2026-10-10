"""Build snapshots from local descriptions using the same ONNX model as Android."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_manifest(directory: Path) -> dict:
    files = {name: {"size": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in ("model.onnx", "tokenizer.json")}
    space = "e5-small-" + files["model.onnx"]["sha256"][:16] + "-" + files["tokenizer.json"]["sha256"][:16]
    return {
        "model_id": "intfloat/multilingual-e5-small", "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "dimension": 384, "embedding_space": space, "max_length": 512,
        "pooling": "masked-mean", "normalization": "l2", "files": files,
    }


class Encoder:
    def __init__(self, directory: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.manifest = model_manifest(directory)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)

    def encode(self, text: str, query: bool = False):
        import numpy as np
        encoded = self.tokenizer.encode(("query: " if query else "passage: ") + text)
        available = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask, "token_type_ids": encoded.type_ids}
        feed = {item.name: np.asarray([available[item.name]], dtype=np.int64) for item in self.session.get_inputs()}
        hidden = self.session.run(None, feed)[0][0]
        mask = np.asarray(encoded.attention_mask, dtype=np.float32)
        pooled = (hidden * mask[:, None]).sum(axis=0) / mask.sum()
        norm = np.linalg.norm(pooled)
        if not np.isfinite(norm) or norm <= 0 or pooled.shape != (384,):
            raise ValueError("invalid model output")
        return (pooled / norm).astype("<f4")


def pack(directory: Path, destination: Path) -> None:
    # Never silently overwrite a previous publication.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("symlinks cannot be published")
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())


def export(catalog: Path, model: Path, output: Path, dataset_id: str, version: int) -> None:
    records = json.loads(catalog.read_text())
    if not isinstance(records, list) or not records:
        raise ValueError("catalog must be a nonempty list")
    encoder = Encoder(model)
    output.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(output / "metadata.sqlite")
    schema = Path(__file__).resolve().parents[3] / "contracts/schema.sql"
    db.executescript(schema.read_text())
    row = 0
    with (output / "vectors.bin").open("wb") as vectors:
        for record in records:
            image_id = record["id"]
            scene = record["scene"]
            if not isinstance(scene, str) or not scene.strip():
                raise ValueError("scene is required")
            thumbnail = None
            if record.get("thumbnail"):
                source = (catalog.parent / record["thumbnail"]).resolve()
                if not source.is_relative_to(catalog.parent.resolve()):
                    raise ValueError("thumbnail must be inside catalog directory")
                from PIL import Image
                (output / "thumbnails").mkdir(exist_ok=True)
                thumbnail = "thumbnails/" + hashlib.sha256(image_id.encode()).hexdigest() + ".jpg"
                with Image.open(source) as image:
                    image.thumbnail((320, 320))
                    image.convert("RGB").save(output / thumbnail, quality=75)
            db.execute("INSERT INTO images VALUES(?,?,?)", (image_id, scene, thumbnail))
            sources = record.get("sources", [])
            if not sources:
                raise ValueError("each image needs at least one source")
            for source in sources:
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    source["key"], image_id, source["chat_id"], source["chat_name"], source.get("sender_id"),
                    source.get("sender_name"), source.get("username"), source.get("topic_id"), source["date"],
                    source.get("link"), source.get("caption", ""),
                ))
            for kind in ("scene", "meaning"):
                text = record.get(kind)
                if text and text.strip():
                    db.execute("INSERT INTO representations VALUES(?,?,?,?)", (row, image_id, kind, text))
                    vectors.write(encoder.encode(text).tobytes())
                    row += 1
            lexical = "\n".join([record.get("ocr", "")] + [s.get("caption", "") for s in sources])
            db.execute("INSERT INTO search_text VALUES(?,?)", (image_id, lexical))
    db.commit()
    db.close()
    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            files[path.relative_to(output).as_posix()] = {"size": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "format_version": 1, "dataset_id": dataset_id, "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(), "embedding_space": encoder.manifest["embedding_space"],
        "model_sha256": encoder.manifest["files"]["model.onnx"]["sha256"],
        "tokenizer_sha256": encoder.manifest["files"]["tokenizer.json"]["sha256"],
        "dimension": 384, "dtype": "float32-le", "normalized": True,
        "vector_count": row, "image_count": len(records), "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    model = commands.add_parser("pack-model")
    model.add_argument("model", type=Path)
    model.add_argument("zip", type=Path)
    build = commands.add_parser("export")
    build.add_argument("catalog", type=Path)
    build.add_argument("model", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--dataset-id", required=True)
    build.add_argument("--version", type=int, required=True)
    archive = commands.add_parser("pack")
    archive.add_argument("directory", type=Path)
    archive.add_argument("zip", type=Path)
    query = commands.add_parser("query")
    query.add_argument("model", type=Path)
    query.add_argument("text")
    query.add_argument("output", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("directory", type=Path)
    publish.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.command == "pack-model":
        (args.model / "model.json").write_text(json.dumps(model_manifest(args.model), indent=2))
        pack(args.model, args.zip)
    elif args.command == "export":
        if args.version < 1:
            parser.error("version must be positive")
        export(args.catalog, args.model, args.output, args.dataset_id, args.version)
    elif args.command == "pack":
        pack(args.directory, args.zip)
    elif args.command == "publish":
        manifest = json.loads((args.directory / "manifest.json").read_text())
        args.folder.mkdir(parents=True, exist_ok=True)
        name = f"snapshot-{manifest['version']}.zip"
        destination = args.folder / name
        pack(args.directory, destination)
        latest = {"dataset_id": manifest["dataset_id"], "version": manifest["version"], "file": name, "size": destination.stat().st_size, "sha256": sha256(destination)}
        temporary = args.folder / "latest.json.tmp"
        temporary.write_text(json.dumps(latest, indent=2))
        temporary.replace(args.folder / "latest.json")
    else:
        encoder = Encoder(args.model)
        args.output.write_text(json.dumps({"text": args.text, "vector": encoder.encode(args.text, query=True).tolist(), "embedding_space": encoder.manifest["embedding_space"], "filters": {}, "top_k": 10}, ensure_ascii=False))


if __name__ == "__main__":
    main()
