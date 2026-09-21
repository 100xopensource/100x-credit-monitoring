import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugins" / "credit-monitoring" / ".claude-plugin" / "plugin.json"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_LINK = "https://github.com/100xopensource/100x-credit-monitoring/releases/tag/v"


class VersionConsistencyTest(unittest.TestCase):
    def test_plugin_manifest_matches_latest_changelog_entry(self):
        manifest_version = json.loads(MANIFEST.read_text())["version"]
        latest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(), re.MULTILINE)
        self.assertIsNotNone(latest, "CHANGELOG.md has no versioned entry")
        self.assertEqual(manifest_version, latest.group(1))

    def test_unreleased_entries_have_no_release_link_and_released_entries_do(self):
        # A version marked "(unreleased)" must not carry a releases/tag link yet
        # (that tag doesn't exist); a version without that marker must, so a
        # changelog entry can't silently claim a release that hasn't happened
        # or drop the link once it has.
        changelog = CHANGELOG.read_text()
        for match in re.finditer(r"^## \[(\d+\.\d+\.\d+)\](.*)$", changelog, re.MULTILINE):
            version, suffix = match.group(1), match.group(2)
            link = f"[{version}]: {RELEASE_LINK}{version}"
            if "unreleased" in suffix.lower():
                self.assertNotIn(link, changelog, f"{version} is marked unreleased but has a release link")
            else:
                self.assertIn(link, changelog, f"{version} has no (unreleased) marker but no release link")
