"""Local native Florence-2 captions and separate Russian/English OCR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile
from urllib.request import ProxyHandler, build_opener

from .observability import LOGGER, Progress, configure_logging

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIRECTORY = PROJECT_ROOT / "models/florence2-base-ft"
OCR_DIRECTORY = PROJECT_ROOT / ".tools/easyocr"
MODEL_MANIFEST = json.loads(Path(__file__).with_name("florence_model.json").read_text())
MODEL_ID = MODEL_MANIFEST["model_id"]
REVISION = MODEL_MANIFEST["revision"]
CAPTION_TASK = "<DETAILED_CAPTION>"
OCR_MODELS = json.loads(Path(__file__).with_name("ocr_models.json").read_text())


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_artifact(directory: Path):
    for name, metadata in MODEL_MANIFEST["files"].items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != metadata["size"] or file_hash(path) != metadata["sha256"]:
            raise ValueError("Florence artifact missing or corrupted; run scripts/setup-florence.sh")


def download_artifact(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    for name, metadata in MODEL_MANIFEST["files"].items():
        path = directory / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size == metadata["size"] and file_hash(path) == metadata["sha256"]:
            continue
        LOGGER.info("Скачивание Florence: %s (%d MiB)", name, metadata["size"] // (1024 * 1024))
        temporary = directory / (name + ".part")
        try:
            digest = hashlib.sha256()
            received = 0
            last_log = 0
            with opener.open(f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}", timeout=120) as response, temporary.open("wb") as out:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > metadata["size"]:
                        raise ValueError("unexpected Florence artifact size")
                    digest.update(block)
                    out.write(block)
                    if received - last_log > 32 * 1024 * 1024:
                        LOGGER.info("Загрузка весов: %d%%", received * 100 // metadata["size"])
                        last_log = received
            if received != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
                raise ValueError("Florence artifact checksum mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    validate_artifact(directory)
    LOGGER.info("Florence: revision и SHA-256 проверены")


def validate_ocr_artifacts(directory=OCR_DIRECTORY):
    for metadata in OCR_MODELS:
        path = Path(directory) / metadata["filename"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != metadata["size"] or file_hash(path) != metadata["sha256"]:
            raise ValueError("OCR weights missing or corrupted; run scripts/setup-florence.sh")


def download_ocr_artifacts(directory=OCR_DIRECTORY):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    for metadata in OCR_MODELS:
        path = directory / metadata["filename"]
        if path.is_file() and not path.is_symlink() and path.stat().st_size == metadata["size"] and file_hash(path) == metadata["sha256"]:
            continue
        archive_path = directory / (metadata["filename"] + ".zip.part")
        staged = directory / (metadata["filename"] + ".part")
        LOGGER.info("Скачивание OCR: %s", metadata["filename"])
        try:
            received = 0
            with opener.open(metadata["url"], timeout=120) as response, archive_path.open("wb") as out:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > 128 * 1024 * 1024:
                        raise ValueError("OCR archive too large")
                    out.write(block)
            with zipfile.ZipFile(archive_path) as archive:
                info = archive.getinfo(metadata["filename"])
                if info.file_size != metadata["size"]:
                    raise ValueError("unexpected OCR weight size")
                with archive.open(info) as source, staged.open("wb") as out:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        out.write(block)
            if file_hash(staged) != metadata["sha256"]:
                raise ValueError("OCR weight checksum mismatch")
            staged.replace(path)
        finally:
            archive_path.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)
    validate_ocr_artifacts(directory)
    LOGGER.info("OCR: SHA-256 весов ru/en проверены")


class FlorenceEncoder:
    def __init__(self, directory=DEFAULT_DIRECTORY, *, device="auto", ocr="easyocr", max_tokens=128):
        import torch
        from transformers import Florence2ForConditionalGeneration, Florence2Processor

        validate_artifact(Path(directory))
        if device not in {"auto", "cuda", "cpu"} or ocr not in {"easyocr", "none", "florence"}:
            raise ValueError("invalid Florence device or OCR backend")
        if not 16 <= max_tokens <= 512:
            raise ValueError("Florence max_tokens must be 16..512")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable in project PyTorch")
        self.device = "cuda" if device == "cuda" or (device == "auto" and torch.cuda.is_available()) else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.torch = torch
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.processor = Florence2Processor.from_pretrained(str(directory), local_files_only=True, trust_remote_code=False, use_fast=False)
        self.model = Florence2ForConditionalGeneration.from_pretrained(str(directory), local_files_only=True, trust_remote_code=False, dtype=self.dtype).to(self.device).eval()
        self.max_tokens = max_tokens
        self.ocr_mode = ocr
        self.reader = None
        if ocr == "easyocr":
            validate_ocr_artifacts()
            # EasyOCR 1.7.2 explicitly requests pickle loading; force tensor-only loading.
            os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "1"
            import easyocr
            self.reader = easyocr.Reader(["ru", "en"], gpu=self.device == "cuda", model_storage_directory=str(OCR_DIRECTORY), user_network_directory=str(OCR_DIRECTORY / "user"), download_enabled=False, verbose=False)
        LOGGER.info("Florence загружена: устройство=%s, OCR=%s, caption tokens=%d", self.device, ocr, max_tokens)

    def _generate(self, image, task, max_tokens):
        inputs = self.processor(images=image, text=task, return_tensors="pt")
        inputs = {key: value.to(device=self.device, dtype=self.dtype) if value.is_floating_point() else value.to(self.device) for key, value in inputs.items()}
        with self.torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=max_tokens, num_beams=1, early_stopping=False, do_sample=False, use_cache=True)
        text = self.processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = self.processor.post_process_generation(text, task=task, image_size=image.size)
        answer = parsed.get(task)
        if not isinstance(answer, str):
            raise ValueError("unexpected Florence output")
        return answer.strip()

    def describe(self, path: Path):
        from .media_image import load_image
        import numpy as np

        image = load_image(path, max_side=1600)
        scene = self._generate(image, CAPTION_TASK, self.max_tokens)
        if not scene:
            scene = self._generate(image, "<CAPTION>", self.max_tokens)
        if not scene:
            raise ValueError("empty Florence caption")
        if self.reader is not None:
            words = self.reader.readtext(np.asarray(image), detail=1, paragraph=False, decoder="greedy", batch_size=8, canvas_size=1600)
            ocr = "\n".join(text for _, text, confidence in words if confidence >= 0.3)
        elif self.ocr_mode == "florence":
            # Optional built-in OCR; Cyrillic quality is not assumed.
            ocr = self._generate(image, "<OCR>", 512)
        else:
            ocr = ""
        return {"scene": scene[:4000], "ocr": ocr[:16000], "meaning": ""}

    @property
    def profile(self):
        return {"backend": "florence", "revision": REVISION, "caption_task": CAPTION_TASK,
                "max_tokens": self.max_tokens, "ocr": self.ocr_mode, "languages": ["ru", "en"],
                "ocr_confidence": 0.3, "max_image_side": 1600, "version": 1,
                "ocr_weights": {m["filename"]: m["sha256"] for m in OCR_MODELS} if self.ocr_mode == "easyocr" else {}}


def describe_catalog(catalog: Path, *, directory=DEFAULT_DIRECTORY, output=None, device="auto", ocr="easyocr", max_tokens=128, limit=None):
    from .collect import write_json
    from .describe import read_catalog

    records = read_catalog(catalog, "исходный каталог")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        records = records[:limit]
    output = Path(output) if output else catalog.with_name("florence-probe-catalog.json" if limit is not None else "florence-catalog.json")
    if output.resolve() == catalog.resolve():
        raise ValueError("output must not overwrite source catalog")
    previous = read_catalog(output, "кеш Florence") if output.exists() else []
    encoder = FlorenceEncoder(directory, device=device, ocr=ocr, max_tokens=max_tokens)
    completed = {record["id"]: record for record in previous if record.get("analysis_model") == MODEL_ID and record.get("analysis_profile") == encoder.profile}
    uncached_total = sum(record["id"] not in completed for record in records)
    results = []
    progress = Progress("Florence", len(records))
    started = time.monotonic()
    generated = cached = 0
    LOGGER.info("Начало Florence: изображений=%d; описания на английском; meaning не генерируется", len(records))
    # Preserve cached records beyond the current prefix if a resumed run is interrupted.
    selected_ids = {r["id"] for r in records}
    checkpoint = {r["id"]: r for r in previous if r["id"] in selected_ids and r.get("analysis_model") == MODEL_ID and r.get("analysis_profile") == encoder.profile}
    for index, record in enumerate(records, 1):
        if record["id"] in completed:
            saved = {**record, **{key: completed[record["id"]][key] for key in ("scene", "ocr", "meaning", "analysis_model", "analysis_profile")}}
            cached += 1
        else:
            path = (catalog.parent / record["image"]).resolve()
            if not path.is_relative_to(catalog.parent.resolve()):
                raise ValueError("image must be inside catalog directory")
            tick = time.monotonic()
            answer = encoder.describe(path)
            saved = {**record, **answer, "analysis_model": MODEL_ID, "analysis_profile": encoder.profile}
            generated += 1
            LOGGER.info("Картинка %d/%d: %.2f с", index, len(records), time.monotonic() - tick)
        results.append(saved)
        checkpoint[record["id"]] = saved
        if record["id"] not in completed or saved != completed[record["id"]]:
            write_json(output, [checkpoint[r["id"]] for r in records if r["id"] in checkpoint])
        elapsed = time.monotonic() - started
        remaining = (uncached_total - generated) * elapsed / generated if generated else 0
        progress.update(index, f"новых={generated}, из кеша={cached}; осталось ~{remaining / 60:.1f} мин", force=True)
    write_json(output, results)
    progress.finish(f"новых={generated}, из кеша={cached}; время={time.monotonic() - started:.1f} с")
    LOGGER.info("Готово: записан Florence-каталог")


def download_main():
    parser = argparse.ArgumentParser(description="Download pinned native Florence safetensors and local OCR weights")
    parser.add_argument("--model-directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()
    configure_logging()
    download_artifact(args.model_directory)
    if not args.skip_ocr:
        download_ocr_artifacts()
