"""Review instructions must not authorize unreviewed engine repairs."""
from pathlib import Path
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/credit-monitoring'


class EngineExecutionBoundaryTest(unittest.TestCase):
    def test_worker_and_orchestrator_follow_shared_boundary(self):
        rules = (PLUGIN / 'CLAUDE.md').read_text()
        self.assertIn('run the shipped engines unchanged', rules)
        self.assertIn('reject results reported as produced by modified engines', rules)
        for name in ('agents/module-analyst.md',
                     'skills/monthly-monitor/references/orchestration.md',
                     'skills/monthly-monitor/references/module-covenant-compliance.md',
                     'skills/monthly-monitor/references/module-three-statement-analysis.md'):
            with self.subTest(path=name):
                text = (PLUGIN / name).read_text()
                self.assertIn('Engine execution boundary', text)
                for old in ('Fix the engine and rerun', 'fix the engine and rerun',
                            'mid-run engine fixes', 'fixed IN THE ENGINE', 'fixed IN THE\nENGINE'):
                    self.assertNotIn(old, text)
