import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools" / "package.py"


def copy_repo(dest):
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def run(repo, command):
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "package.py"), command],
        cwd=repo, capture_output=True, text=True, check=True,
    )


class PackageBuildTest(unittest.TestCase):
    def test_build_writes_the_bundle_and_checksum_file_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = copy_repo(Path(tmp) / "repo")
            bundle = repo / "credit-monitoring.plugin"
            sums = repo / "SHA256SUMS.txt"
            bundle.unlink()
            sums.unlink()

            run(repo, "build")

            self.assertTrue(bundle.exists())
            self.assertTrue(sums.exists())
            expected = hashlib.sha256(bundle.read_bytes()).hexdigest() + "  credit-monitoring.plugin\n"
            self.assertEqual(sums.read_bytes(), expected.encode("utf-8"))
            run(repo, "check")

    def test_check_does_not_modify_the_bundle_or_checksum_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = copy_repo(Path(tmp) / "repo")
            bundle = repo / "credit-monitoring.plugin"
            sums = repo / "SHA256SUMS.txt"
            before_bundle, before_sums = bundle.read_bytes(), sums.read_bytes()

            run(repo, "check")

            self.assertEqual(bundle.read_bytes(), before_bundle)
            self.assertEqual(sums.read_bytes(), before_sums)


if __name__ == "__main__":
    unittest.main()
