"""Failed input reads keep local diagnostics out of portable results."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/credit-monitoring'
sys.path.insert(0, str(PLUGIN / 'lib'))
sys.path.insert(0, str(PLUGIN / 'skills/monthly-monitor/references'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / 'skills/monthly-monitor/references' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FailureRecordsTest(unittest.TestCase):
    def test_model_read_failure_keeps_status_without_local_path(self):
        module = load('three_statement_analysis')
        config = {'borrower_name': 'Example', 'model': {'scale': 1, 'currency': 'USD'}}
        with patch.object(sys, 'argv', ['engine', '--borrower-config', 'config.json', '--model', 'store:Example/model.xlsx', '--out', 'result']), \
             patch.object(module.ml, 'load_config', return_value=config), \
             patch.object(module.ml, 'resolve_path', return_value='/private-input/model.xlsx'), \
             patch.object(module.ml, 'open_model', side_effect=OSError('cannot open /private-input/model.xlsx')), \
             patch.object(module.ml, 'write_output') as writer:
            module.main()
        record = writer.call_args.args[0]
        self.assertNotIn('/private-input', json.dumps(record))
        self.assertTrue(record['skipped'])
        self.assertEqual(record['exceptions'][0]['flag'], 'model_open_failed')

    def test_optional_input_failures_keep_input_identity_without_machine_path(self):
        module = load('revenue_quality')
        cfg = {'borrower_name': 'Example', 'model': {'scale': 1, 'currency': 'USD'}, 'paths': {}}
        with patch.object(module.ml, 'resolve_path', return_value='/private-input/source.xlsx'), \
             patch.object(module, 'parse_retention_analysis', side_effect=OSError('/private-input/source.xlsx')), \
             patch.object(module, 'parse_deferred_revenue', side_effect=OSError('/private-input/source.xlsx')):
            result = module.compute(cfg, '2026-06', module.ml.reporting_unit(cfg),
                                    retention_file='source:retention.xlsx', deferred_revenue_file='source:deferred.xlsx')
        self.assertNotIn('/private-input', json.dumps(result))
        errors = [e for e in result['exceptions'] if e.get('type') == 'parse_error']
        self.assertEqual({e['input'] for e in errors}, {'retention_file', 'deferred_revenue_file'})
