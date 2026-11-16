"""A setting changed after a month is accepted must not block the next month.

monitor-setup rewrites a borrower's settings under `.record/current/config/`,
for example the budget map when a new budget arrives. The next run snapshots
that setting and finishing writes the same bytes back, so finish must let it
through. Without that fix (upstream commit 599069a) finish refuses it as "unrecorded
work", and nothing in the run lets the analyst past it.

Every step runs through the plugin's own command-line tools in fresh
processes, as the monthly-monitor skill runs them.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import openpyxl

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/credit-monitoring'
RUN_RECORD = PLUGIN / 'skills/monthly-monitor/references/run_record.py'
MANIFEST = PLUGIN / 'lib/manifest.py'

BORROWER = 'example-borrower'
CURRENT = f'store:borrowers/{BORROWER}/.record/current'
MODEL = 'model.xlsx'
MEMO = 'memo.html'


def write_model(path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Income Statement'
    sheet['A6'], sheet['B6'], sheet['C6'] = 'Line', 'Sep 2026', 'Aug 2026'
    sheet['A7'], sheet['B7'], sheet['C7'] = 'Revenue', 100, 90
    book.save(path)


class SettingChangedAfterAcceptanceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        source = base / 'source'
        root = base / 'store'
        source.mkdir()
        root.mkdir()
        (root / 'store-schema.json').write_text(
            json.dumps({'schema': 'credit-monitoring-store/v1', 'schema_version': 1}),
            encoding='utf-8')
        self.env = dict(os.environ, CREDIT_MONITOR_STORE_ROOT=str(root),
                        CREDIT_MONITOR_SOURCE_ROOT=str(source))

        self.borrower = root / 'borrowers' / BORROWER
        config = self.borrower / '.record/current/config'
        model_dir = self.borrower / '_model'
        config.mkdir(parents=True)
        model_dir.mkdir()
        self.config = config / 'borrower-config.json'
        self.config.write_text(json.dumps({
            'borrower_name': 'Example Borrower',
            'model': {'scale': 1, 'currency': 'USD'},
            'paths': {
                'model_output_path': f'store:borrowers/{BORROWER}/_model/{MODEL}',
                'monitor_output_folder': CURRENT,
                'budget_map_path': f'{CURRENT}/config/budget-map.json',
            },
        }), encoding='utf-8')
        self.budget_map = config / 'budget-map.json'
        self.budget_map.write_text(json.dumps({'metric_rows': {'ebitda': 9}}), encoding='utf-8')
        self.model = model_dir / MODEL
        write_model(self.model)
        for sidecar in ('source-map.json', 'restatements.json'):
            (model_dir / sidecar).write_text(json.dumps({'version': 1}), encoding='utf-8')

    def cli(self, script, *args):
        done = subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
                              env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(done.returncode, 0,
                         f'{script.name} {args[0]} failed:\n{done.stderr or done.stdout}')
        return done.stdout

    def month(self, period, *base):
        """One month, open to select, as monthly-monitor runs it."""
        opened = json.loads(self.cli(RUN_RECORD, 'open', '--folder', CURRENT,
                                     '--period', period, '--modules', '', *base, '--json'))
        run = Path(opened['run_folder'])
        self.cli(RUN_RECORD, 'snapshot', '--run', run, '--config', self.config,
                 '--model', self.model)
        (run / MEMO).write_text(f'<html>{period} memo</html>', encoding='utf-8')
        for step in ('coverage', 'memo', 'write_back'):
            self.cli(RUN_RECORD, 'step', '--run', run, '--step', step)
        self.cli(MANIFEST, 'finish', '--run', run, '--deal', self.borrower,
                 '--period', period, '--workbook', MODEL, '--memo', MEMO)
        self.cli(RUN_RECORD, 'select', '--run', run, '--deal', self.borrower,
                 '--period', period)
        return run

    def test_next_month_finishes_after_a_setting_changes(self):
        august = self.month('2026-08', '--seed-base')

        # monitor-setup maps the borrower's new budget after August is accepted.
        new_map = json.dumps({'metric_rows': {'ebitda': 5}})
        self.budget_map.write_text(new_map, encoding='utf-8')

        september = self.month('2026-09', '--base-run', august.name, '--base-period', '2026-08')

        self.assertEqual((september / 'config/budget-map.json').read_text(encoding='utf-8'),
                         new_map)
        self.assertEqual(self.budget_map.read_text(encoding='utf-8'), new_map)


if __name__ == '__main__':
    unittest.main()
