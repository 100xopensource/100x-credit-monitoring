"""Portable evidence locations must hold on early-return paths too."""
import importlib.util
from pathlib import Path
import sys
import unittest

LIB = Path(__file__).resolve().parents[1] / 'plugins/credit-monitoring/lib'
sys.path.insert(0, str(LIB))
spec = importlib.util.spec_from_file_location('portable_source_register', LIB / 'source_register.py')
register = importlib.util.module_from_spec(spec)
spec.loader.exec_module(register)


class PortableEvidenceTest(unittest.TestCase):
    def test_early_returns_omit_machine_file_locations(self):
        for options in ({'derived': True}, {'not_found': {'cash': 'Unavailable'}}):
            for location in ('/private-input/source.xlsx', '../source.xlsx', r'C:\private-input\source.xlsx'):
                with self.subTest(options=options, location=location):
                    result = register._evidence(dict(file=location, **options), 'cash', 'A1', 1, '.', '2026-06')
                    self.assertIsNone(result['file'])
                    self.assertIn(result['method'], ('generated', 'search'))

    def test_missing_evidence_keeps_relative_location(self):
        result = register._evidence({'file': 'reports/source.xlsx', 'not_found': {'cash': 'Unavailable'}},
                                    'cash', 'A1', 1, '.', '2026-06')
        self.assertEqual(result['file'], 'reports/source.xlsx')
        self.assertEqual(result['status'], 'unresolved')
