"""Checks for the shipped sample, without running monitoring or changing saved state."""
import hashlib
import json
from pathlib import Path
import os
import re
import unittest
import subprocess
import tempfile
import shutil
from urllib.parse import unquote
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'sample-output'


class SampleOutputTest(unittest.TestCase):
    def test_public_sample_is_git_trackable(self):
        public = ROOT
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            shutil.copyfile(public / '.gitignore', checkout / '.gitignore')
            git_env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
            subprocess.run(['git', 'init', '-q', str(checkout)], check=True, env=git_env)
            names = [p.relative_to(public).as_posix() for p in (public / 'sample-output').rglob('*') if p.is_file()]
            self.assertTrue(names)
            for name in names:
                path = checkout / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            subprocess.run(['git', 'add', '.'], cwd=checkout, check=True, env=git_env)
            tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=checkout, env=git_env).decode().split('\0')
            self.assertTrue(set(names) <= set(tracked), set(names) - set(tracked))

    def test_sample_payload_and_links(self):
        self.assertTrue((SAMPLE / 'sample-manifest.json').is_file(), 'sample provenance is required')
        manifest = json.loads((SAMPLE / 'sample-manifest.json').read_text())
        payload = sorted(p for p in SAMPLE.rglob('*') if p.is_file()
                         and (p.name == 'portfolio-monitor.html' or 'outputs' in p.relative_to(SAMPLE).parts))
        listing = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(SAMPLE).as_posix()}\n'
                          for p in payload)
        self.assertEqual(manifest['payload_sha256'], hashlib.sha256(listing.encode()).hexdigest())
        self.assertEqual(manifest['payload_file_count'], len(payload))
        # Input dataset lives in its own repository; staging checks its fingerprint.
        store = SAMPLE / 'outputs/credit-monitoring-output'
        pages = store / 'artifacts/portfolio/Artifact'
        html = (SAMPLE / 'portfolio-monitor.html').read_text()
        embedded = json.JSONDecoder().raw_decode(html.split('PM.embedded = ', 1)[1])[0]
        self.assertEqual(19, len(embedded))
        for name, text in embedded.items():
            self.assertEqual((pages / name).read_text(), text, name)
        borrowers = json.loads((pages / 'index.json').read_text())['borrowers']
        self.assertEqual(6, len(borrowers))
        for row in borrowers:
            borrower = store / 'borrowers' / row['slug']
            runs = list((borrower / '.record/runs').glob('*/*/manifest.json'))
            self.assertEqual(1, len(runs))
            run = runs[0].parent
            saved = json.loads(runs[0].read_text())
            for name, digest in saved['files'].items():
                self.assertEqual(digest, 'sha256:' + hashlib.sha256((run / name).read_bytes()).hexdigest(), name)
            for role in ('financial_workbook', 'memo'):
                name = saved['roles'][role]
                self.assertEqual((run / name).read_bytes(), (borrower / name).read_bytes())
            self.assertEqual((run / saved['roles']['memo']).read_bytes(), (pages / row['files']['memo']).read_bytes())
            with ZipFile(borrower / saved['roles']['financial_workbook']) as xlsx:
                self.assertIsNone(xlsx.testzip())
        for path in SAMPLE.rglob('*'):
            self.assertFalse(path.is_symlink(), path)
            self.assertNotIn(path.name, {'.DS_Store', 'hosted-artifact.json', 'store.lock'})
            self.assertNotEqual('.bak', path.suffix)
        readme = (SAMPLE / 'README.md').read_text()
        self.assertIn('https://100xpartners.ai/credit-monitoring/', readme)
        self.assertIn('https://github.com/100xopensource/100x-credit-monitoring-synthetic-dataset', readme)
        for link in re.findall(r'\]\(([^)]+)\)', readme):
            if not link.startswith(('https://', '#')):
                self.assertTrue((SAMPLE / unquote(link.split('#')[0])).is_file(), link)
        with ZipFile(ROOT / 'credit-monitoring.plugin') as plugin:
            self.assertFalse(any('sample-output/' in name for name in plugin.namelist()))


if __name__ == '__main__':
    unittest.main()
