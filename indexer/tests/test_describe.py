import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tg_indexer.describe import describe, open_vlm_response, VLMHTTPError, VLMOutputError, parse_description_response


class DescriptionTests(unittest.TestCase):
    def test_context_overflow_is_actionable_without_echoing_server_data(self):
        body = json.dumps({'error': json.dumps({'error': {
            'message': 'request (4164 tokens) exceeds available context size (4096 tokens) private_caption',
        }})}).encode()
        error = VLMHTTPError(400, body)
        self.assertIn('HTTP 400', str(error))
        self.assertIn('контекст', str(error))
        self.assertNotIn('private_caption', str(error))

    def test_http_adapter_preserves_status_and_safe_category(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise HTTPError('http://127.0.0.1:11434/api/generate', 500, 'private', {},
                                io.BytesIO(b'{"error":"not enough memory private_path"}'))

        with self.assertRaises(VLMHTTPError) as caught:
            open_vlm_response(Opener(), None)
        self.assertEqual(caught.exception.status, 500)
        self.assertIn('RAM/VRAM', str(caught.exception))
        self.assertNotIn('private_path', str(caught.exception))

    def test_unknown_error_body_is_not_logged(self):
        for body in [b'private text', b'{"error":"private_caption"}', b'[]']:
            error = VLMHTTPError(503, body)
            self.assertIn('HTTP 503', str(error))
            self.assertNotIn('private_caption', str(error))
            self.assertNotIn('private text', str(error))

    def test_explicit_context_is_sent_and_saved_results_resume(self):
        requests = []
        answer = {'scene': 'Синтетический квадрат', 'ocr': '', 'meaning': ''}

        class Opener:
            def open(self, request, timeout):
                requests.append(json.loads(request.data))
                return io.BytesIO(json.dumps({'response': json.dumps(answer)}).encode())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'image.png').write_bytes(b'synthetic')
            catalog = root/'catalog.json'
            catalog.write_text(json.dumps([{'id': 'synthetic', 'image': 'image.png'}]))
            with patch('urllib.request.build_opener', return_value=Opener()):
                describe(catalog, 'http://127.0.0.1:11434', 'synthetic-model')
                describe(catalog, 'http://127.0.0.1:11434', 'synthetic-model')
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['options']['num_ctx'], 8192)
            self.assertEqual(requests[0]['options']['num_predict'], 1024)
            self.assertFalse(requests[0]['think'])
            self.assertEqual(requests[0]['options']['repeat_penalty'], 1.1)
            self.assertEqual(requests[0]['format']['required'], ['scene', 'ocr', 'meaning'])
            saved=json.loads((root/'described-catalog.json').read_text())
            self.assertEqual(saved[0]['scene'], answer['scene'])

    def test_context_bounds_are_checked_before_catalog_read(self):
        for value in (0, 409.5, True, 32769):
            with self.assertRaisesRegex(ValueError, 'context_size'):
                describe(Path('does-not-exist'), 'http://127.0.0.1:11434', 'synthetic', context_size=value)

    def test_timeout_bounds_are_checked_before_catalog_read(self):
        for value in (0, True, 3601):
            with self.assertRaisesRegex(ValueError, 'timeout'):
                describe(Path('does-not-exist'), 'http://127.0.0.1:11434', 'synthetic', timeout=value)

    def test_thinking_only_output_is_not_treated_as_description(self):
        body = json.dumps({'response': '', 'thinking': 'private reasoning', 'done': True, 'done_reason': 'length'}).encode()
        with self.assertRaises(VLMOutputError) as caught:
            parse_description_response(body)
        self.assertIn('пустой', str(caught.exception))
        self.assertIn('length', str(caught.exception))
        self.assertNotIn('private reasoning', str(caught.exception))

    def test_final_answer_is_parsed_and_thinking_is_ignored(self):
        answer = {'scene': 'synthetic', 'ocr': '', 'meaning': ''}
        body = json.dumps({'response': json.dumps(answer), 'thinking': 'private reasoning', 'done': True}).encode()
        self.assertEqual(parse_description_response(body), answer)

    def test_invalid_envelope_and_description_are_distinguished(self):
        for body, text in [(b'not json', 'ответ HTTP'), (b'[]', 'объектом'),
                           (json.dumps({'response': '{"scene":', 'done_reason': 'length'}).encode(), 'полным JSON')]:
            with self.assertRaises(VLMOutputError) as caught:
                parse_description_response(body)
            self.assertIn(text, str(caught.exception))

    def test_empty_answer_retries_with_larger_budget(self):
        requests = []
        answer = {'scene': 'synthetic', 'ocr': '', 'meaning': ''}

        class Opener:
            def open(self, request, timeout):
                requests.append(json.loads(request.data))
                data = {'response': '', 'thinking': 'private', 'done_reason': 'length'} if len(requests) == 1 else {'response': json.dumps(answer)}
                return io.BytesIO(json.dumps(data).encode())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'image.png').write_bytes(b'synthetic')
            catalog = root/'catalog.json'
            catalog.write_text(json.dumps([{'id': 'synthetic', 'image': 'image.png'}]))
            with patch('urllib.request.build_opener', return_value=Opener()), self.assertLogs('tg_indexer', level='WARNING'):
                describe(catalog, 'http://127.0.0.1:11434', 'synthetic')
            self.assertEqual([r['options']['num_predict'] for r in requests], [1024, 2048])
            self.assertFalse(requests[0]['think'])
            self.assertTrue((root/'described-catalog.json').exists())

    def test_persistent_invalid_output_stops_without_overwriting_cache(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(b'{"response":"", "thinking":"private", "done_reason":"length"}')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'image.png').write_bytes(b'synthetic')
            catalog = root/'catalog.json'
            catalog.write_text(json.dumps([{'id': 'synthetic', 'image': 'image.png'}]))
            existing = root/'described-catalog.json'
            existing.write_text('[]')
            with patch('urllib.request.build_opener', return_value=Opener()), self.assertLogs('tg_indexer', level='WARNING'):
                with self.assertRaisesRegex(VLMOutputError, 'две попытки'):
                    describe(catalog, 'http://127.0.0.1:11434', 'synthetic')
            self.assertEqual(len(requests), 2)
            self.assertEqual(existing.read_text(), '[]')

    def test_bad_catalog_identifies_stage_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory)/'catalog.json'
            catalog.write_text('private invalid JSON')
            with self.assertRaisesRegex(VLMOutputError, 'исходный каталог') as caught:
                describe(catalog, 'http://127.0.0.1:11434', 'synthetic')
            self.assertNotIn('private', str(caught.exception))

    def test_description_limits_are_enforced_without_echoing_content(self):
        for key, maximum in [('scene', 1000), ('ocr', 2000), ('meaning', 600)]:
            answer = {'scene': 'synthetic', 'ocr': '', 'meaning': ''}
            answer[key] = 'x' * (maximum + 1)
            with self.assertRaisesRegex(VLMOutputError, 'ограничение длины'):
                parse_description_response(json.dumps({'response': json.dumps(answer)}).encode())
