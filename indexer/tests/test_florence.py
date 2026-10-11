import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tg_indexer.florence import describe_catalog, MODEL_ID, validate_artifact


class FakeEncoder:
    calls = []
    fail = False
    profile = {'version': 1, 'backend': 'florence', 'ocr': 'none'}

    def __init__(self, *args, **kwargs):
        pass

    def describe(self, path):
        self.calls.append(path.name)
        if self.fail:
            raise RuntimeError('synthetic error')
        return {'scene': 'A person sleeping on a couch.', 'ocr': 'TEST', 'meaning': ''}


class FlorenceTests(unittest.TestCase):
    def setUp(self):
        FakeEncoder.calls = []
        FakeEncoder.fail = False

    def catalog(self, root, count=2):
        catalog = root/'selected-catalog.json'
        records=[]
        for i in range(count):
            name=f'image-{i}.png'
            (root/name).write_bytes(b'synthetic')
            records.append({'id':str(i),'image':name,'sources':[{'key':str(i),'caption':'private caption'}]})
        catalog.write_text(json.dumps(records))
        return catalog

    def test_florence_preserves_contract_and_sources_and_uses_separate_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root)
            legacy=root/'described-catalog.json';legacy.write_text('[]')
            with patch('tg_indexer.florence.FlorenceEncoder', FakeEncoder):
                describe_catalog(catalog, ocr='none')
            result=json.loads((root/'florence-catalog.json').read_text())
            self.assertEqual(len(result),2)
            self.assertEqual(result[0]['meaning'],'')
            self.assertEqual(result[0]['analysis_model'],MODEL_ID)
            self.assertEqual(result[0]['sources'][0]['caption'],'private caption')
            self.assertEqual(legacy.read_text(),'[]')

    def test_matching_profile_resumes_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root)
            with patch('tg_indexer.florence.FlorenceEncoder', FakeEncoder):
                describe_catalog(catalog, ocr='none')
                describe_catalog(catalog, ocr='none')
            self.assertEqual(FakeEncoder.calls,['image-0.png','image-1.png'])

    def test_changed_profile_reprocesses_cached_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root)
            with patch('tg_indexer.florence.FlorenceEncoder', FakeEncoder):
                describe_catalog(catalog, ocr='none')
                output=root/'florence-catalog.json';data=json.loads(output.read_text())
                for r in data:r['analysis_profile']={'version':0}
                output.write_text(json.dumps(data))
                describe_catalog(catalog, ocr='none')
            self.assertEqual(len(FakeEncoder.calls),4)

    def test_failure_does_not_discard_cached_entries_beyond_current_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,3)
            cached={'id':'2','scene':'cached','ocr':'','meaning':'','analysis_model':MODEL_ID,'analysis_profile':FakeEncoder.profile,'sources':[]}
            output=root/'florence-catalog.json';output.write_text(json.dumps([cached]))
            original=FakeEncoder.describe
            def fail_second(instance,path):
                if path.name=='image-1.png':raise RuntimeError('synthetic')
                return original(instance,path)
            with patch('tg_indexer.florence.FlorenceEncoder',FakeEncoder),patch.object(FakeEncoder,'describe',fail_second):
                with self.assertRaises(RuntimeError):describe_catalog(catalog,ocr='none')
            result=json.loads(output.read_text())
            self.assertEqual([r['id'] for r in result],['0','2'])

    def test_traversal_is_rejected_before_reading_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,1)
            data=json.loads(catalog.read_text());data[0]['image']='../outside.png';catalog.write_text(json.dumps(data))
            with patch('tg_indexer.florence.FlorenceEncoder',FakeEncoder):
                with self.assertRaisesRegex(ValueError,'inside catalog'):describe_catalog(catalog)
            self.assertEqual(FakeEncoder.calls,[])

    def test_output_cannot_replace_source_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,1)
            with self.assertRaisesRegex(ValueError,'overwrite source'):describe_catalog(catalog,output=catalog)

    def test_corrupt_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'artifact'):validate_artifact(Path(directory))

    def test_probe_limit_preserves_full_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,2)
            with patch('tg_indexer.florence.FlorenceEncoder',FakeEncoder):
                describe_catalog(catalog,ocr='none')
                describe_catalog(catalog,ocr='none',limit=1)
            self.assertEqual(len(json.loads((root/'florence-catalog.json').read_text())),2)
            self.assertEqual(len(json.loads((root/'florence-probe-catalog.json').read_text())),1)

    def test_sources_are_refreshed_when_caption_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,1)
            with patch('tg_indexer.florence.FlorenceEncoder',FakeEncoder):
                describe_catalog(catalog,ocr='none')
                data=json.loads(catalog.read_text());data[0]['sources'][0]['caption']='changed';catalog.write_text(json.dumps(data))
                describe_catalog(catalog,ocr='none')
            self.assertEqual(FakeEncoder.calls,['image-0.png'])
            result=json.loads((root/'florence-catalog.json').read_text())
            self.assertEqual(result[0]['sources'][0]['caption'],'changed')

    def test_cli_defaults_to_local_florence(self):
        from tg_indexer.describe import main
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);catalog=self.catalog(root,1)
            with patch('sys.argv',['tg-describe',str(catalog)]),patch('tg_indexer.describe.configure_logging'),patch('tg_indexer.florence.describe_catalog') as run:
                main()
            self.assertEqual(run.call_args.args,(catalog,))
            self.assertEqual(run.call_args.kwargs['max_tokens'],128)
            self.assertEqual(run.call_args.kwargs['ocr'],'easyocr')
