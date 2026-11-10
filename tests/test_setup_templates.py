"""Setup templates remain consistent illustrative inputs, not borrower settings."""
import json
from pathlib import Path
import unittest

CONFIG = Path(__file__).resolve().parents[1] / 'plugins/credit-monitoring/config'


class SetupTemplatesTest(unittest.TestCase):
    def test_neutral_mapping_shapes_and_optional_adjustments(self):
        borrower = json.loads((CONFIG / 'borrower-config.template.json').read_text())
        budget = json.loads((CONFIG / 'budget-map.template.json').read_text())
        self.assertEqual(borrower['comparability_items'], [])
        keys = lambda obj: {k for k in obj if not k.startswith('_')}
        self.assertEqual(keys(borrower['opex_groups']), keys(budget['opex_groups']))
        for key in keys(borrower['opex_groups']):
            self.assertIsInstance(borrower['opex_groups'][key], str)
            self.assertIsInstance(budget['opex_groups'][key], int)
        self.assertIn('revenue', budget['metric_rows'])
        self.assertNotIsInstance(borrower['model']['scale'], (int, float))

    def test_agreement_examples_require_real_citations(self):
        spec = json.loads((CONFIG / 'covenant-spec.template.json').read_text())
        examples = spec['_examples']
        self.assertIn('Not read by the module', examples['_note'])
        deemed = examples['definitions_example']['EBITDA']['deemed_schedule']['agreement_ref']
        self.assertEqual(deemed['page'], 0)
        self.assertTrue(deemed['quote'].startswith('<'))
