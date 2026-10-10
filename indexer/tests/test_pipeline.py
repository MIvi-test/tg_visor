import json
from pathlib import Path
import tempfile
import unittest

from tg_indexer.describe import describe
from tg_indexer.fixture import build
from tg_indexer.pipeline import pack
from tg_indexer.collect import scope_records


class PipelineTests(unittest.TestCase):
    def test_changed_config_excludes_old_chats_and_dates(self):
        records = [{"id": "image", "sources": [
            {"chat_id": "selected", "date": 100},
            {"chat_id": "selected", "date": 200},
            {"chat_id": "excluded", "date": 200},
        ]}]
        selected = scope_records(records, {"selected"}, 150, 250)
        self.assertEqual(selected[0]["sources"], [{"chat_id": "selected", "date": 200}])
        self.assertEqual(len(records[0]["sources"]), 3)

    def test_fixture_matches_vector_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            build(root)
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual((root / "vectors.bin").stat().st_size, manifest["vector_count"] * 384 * 4)
            self.assertEqual(manifest["embedding_space"], "fixture-one-hot-v1")

    def test_pack_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            build(root)
            target = Path(temporary) / "fixture.zip"
            pack(root, target)
            with self.assertRaises(FileExistsError):
                pack(root, target)

    def test_vlm_rejects_remote_endpoints_before_reading_data(self):
        for endpoint in ("https://example.org", "http://example.org", "http://localhost@example.org"):
            with self.assertRaises(ValueError):
                describe(Path("does-not-exist"), endpoint, "model")


if __name__ == "__main__":
    unittest.main()
