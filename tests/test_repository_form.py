import json
import re
import subprocess
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "credit-monitoring"
CANONICAL = "https://github.com/100xopensource/100x-credit-monitoring"
REQUIRED = (
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
    ".github/workflows/validate.yml",
)
PUBLIC_TEXT = (
    "README.md",
    "plugins/credit-monitoring/README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    # docs/ is reader-facing and linked from the README, so it carries install
    # URLs too. Left out of this tuple, a non-canonical URL pasted into
    # GETTING_STARTED.md passes CI while the same URL in README.md fails.
    "docs/GETTING_STARTED.md",
    "docs/HOW_IT_WORKS.md",
    "docs/LIMITATIONS.md",
    "docs/SECURITY_AND_PRIVACY.md",
)


def tracked_github_files():
    paths = subprocess.check_output(
        ["git", "ls-files", ".github"], cwd=ROOT, text=True
    ).splitlines()
    return [ROOT / path for path in paths]


class RepositoryFormTest(unittest.TestCase):
    def test_first_review_discard_instructions_match_runtime(self):
        paths = [PLUGIN / "README.md", ROOT / "docs/GETTING_STARTED.md",
                 ROOT / "docs/HOW_IT_WORKS.md",
                 PLUGIN / "skills/monthly-monitor/SKILL.md"]
        for path in paths:
            with self.subTest(path=path):
                text = " ".join(path.read_text().lower().split())
                self.assertIn("the first review cannot be discarded", text)
                self.assertNotIn("discard removes the draft deliverables", text)
        self.assertIn("the first review cannot be discarded",
                      (PLUGIN / "lib/run_lib.py").read_text())

    def test_public_repository_surface_is_complete(self):
        missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
        self.assertEqual([], missing)

    def test_public_links_use_canonical_repository(self):
        files = [ROOT / path for path in PUBLIC_TEXT if (ROOT / path).is_file()]
        files += tracked_github_files()
        text = "\n".join(path.read_text() for path in files)
        repository_urls = set(re.findall(r"https://github\.com/[^\s)\]]+/100x-credit-monitoring[^\s)\]]*", text))
        self.assertTrue(repository_urls)
        self.assertTrue(all(url.startswith(CANONICAL) for url in repository_urls), repository_urls)

    def test_readme_has_public_trust_cues(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn(f"{CANONICAL}/actions/workflows/validate.yml/badge.svg", readme)
        self.assertIn(f"{CANONICAL}/releases/latest", readme)
        for path in ("CHANGELOG.md", "CODE_OF_CONDUCT.md", "SUPPORT.md"):
            self.assertIn(f"]({path})", readme)

    def test_public_copy_has_no_staging_identity(self):
        files = [ROOT / path for path in PUBLIC_TEXT if (ROOT / path).is_file()]
        files += tracked_github_files()
        text = "\n".join(path.read_text() for path in files)
        for term in ("private stage 1", "preview repository", "public repository handoff", "release/v1.0.0", "nguyen-tran-100x", "rc-03", "rc-04"):
            self.assertNotIn(term, text.lower())

    def test_generated_plugin_is_tracked_and_current(self):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "credit-monitoring.plugin"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(0, tracked.returncode, tracked.stderr)
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "credit-monitoring.plugin"],
            cwd=ROOT,
            check=False,
        )
        self.assertNotEqual(0, ignored.returncode)
        # A tracked bundle can go stale against the sources it was built from.
        # package.py check rebuilds and refuses to pass on a mismatch.
        check = subprocess.run(
            ["python3", "tools/package.py", "check"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(0, check.returncode, check.stderr or check.stdout)

    def test_marketplace_catalog_lists_this_plugin(self):
        catalog = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        # Cowork renders the card's byline from the marketplace name, not from
        # owner.name. Naming it after the repository would print the product
        # name twice; the marketplace has to read as the publisher.
        self.assertEqual("100xopensource", catalog["name"])
        self.assertNotEqual(manifest["name"], catalog["name"])
        self.assertTrue(catalog["owner"]["url"].startswith(CANONICAL.rsplit("/", 1)[0]))
        self.assertEqual(1, len(catalog["plugins"]))
        entry = catalog["plugins"][0]
        self.assertEqual(manifest["name"], entry["name"])
        self.assertEqual("./plugins/credit-monitoring", entry["source"])
        resolved = ROOT / entry["source"]
        self.assertTrue((resolved / ".claude-plugin" / "plugin.json").is_file())

    def test_marketplace_catalog_stays_out_of_the_bundle(self):
        output = ROOT / "credit-monitoring.plugin"
        original = output.read_bytes() if output.exists() else None
        try:
            build = subprocess.run(
                ["python3", "tools/package.py", "build"],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, build.returncode, build.stderr or build.stdout)
            with ZipFile(output) as archive:
                names = archive.namelist()
                advertised = [
                    name for name in names
                    if "crash course" in archive.read(name).decode(errors="ignore").lower()
                    or "credit-monitoring-crash-course" in archive.read(name).decode(errors="ignore")
                ]
            self.assertIn(".claude-plugin/plugin.json", names)
            self.assertNotIn(".claude-plugin/marketplace.json", names)
            self.assertFalse(any("crash-course" in name for name in names))
            self.assertEqual([], advertised)
        finally:
            if original is None:
                output.unlink(missing_ok=True)
            else:
                output.write_bytes(original)

    def test_ost66_public_welcome_and_onboarding_contract(self):
        plugin = ROOT / "plugins" / "credit-monitoring"
        entry = (plugin / "references" / "entry-routing.md").read_text()
        asking = (plugin / "references" / "asking.md").read_text()
        folders = (plugin / "references" / "connecting-folders.md").read_text()
        store = (plugin / "skills" / "store-setup" / "SKILL.md").read_text()
        run = (plugin / "skills" / "run-monitor" / "SKILL.md").read_text()
        setup = (plugin / "skills" / "monitor-setup" / "SKILL.md").read_text()
        guide = (plugin / "skills" / "open-folder-guide" / "SKILL.md").read_text()
        no_name = entry.split(
            "### No borrower named, no reachable borrower records", 1
        )[1].split("### Borrower named, monitoring store not reachable", 1)[0]
        named = entry.split(
            "### Borrower named, monitoring store not reachable", 1
        )[1].split("### `new`", 1)[0]
        access = asking.split("**Before asking for the folders**", 1)[1].split(
            "**When the request does not work**", 1
        )[0]
        new = entry.split("### `new`", 1)[1].split("### `setup_incomplete`", 1)[0]
        normalized_access = " ".join(access.lower().split())

        for welcome in (no_name, named, new):
            normalized_welcome = " ".join(welcome.lower().replace(">", "").split())
            self.assertIn('> "Welcome to Credit Monitoring.', welcome)
            self.assertGreaterEqual(welcome.count("\n>\n"), 2)
            self.assertIn("> - **Financial workbook**", welcome)
            self.assertIn("> - **Credit monitoring memo**", welcome)
            for phrase in (
                "financial reporting", "loan documents", "performance", "liquidity",
                "covenants", "risks", "missing information", "attached here",
                "where they were saved",
            ):
                self.assertIn(phrase, normalized_welcome)
            self.assertNotIn("pass their checks", normalized_welcome)
        self.assertIn(
            "**[Try sample data (recommended)]** / **[Use my own documents]** / "
            "**[Not now]**",
            no_name,
        )
        self.assertNotIn("[Use my own documents]", named)
        self.assertNotIn("Help me prepare my folders", no_name)
        self.assertIn(
            "https://github.com/100xopensource/100x-credit-monitoring-synthetic-dataset",
            no_name,
        )
        for phrase in (
            "full synthetic borrower set",
            "minified versions",
            "repository documentation",
            "do not guess",
            "without repeating the welcome",
            "one borrower",
        ):
            self.assertIn(phrase, no_name.lower())
        self.assertIn(
            "The sample download isn't available at that page yet. You can use your own "
            "documents, use a sample you already downloaded, or pause for now.",
            no_name,
        )
        self.assertIn(
            "**[Use my own documents]** / **[Use an already-downloaded sample]** / "
            "**[Not now]**",
            no_name,
        )
        self.assertNotIn("skill’s rules", entry.lower())
        self.assertNotIn("skill's rules", entry.lower())
        self.assertIn("free text", asking.lower())
        self.assertNotIn("**[Type the name]**", asking)
        self.assertNotIn("**[Not sure yet]**", asking)
        setup = " ".join(setup.lower().split())
        for phrase in (
            "after the source folder is approved",
            "two candidates",
            "more companies",
            "none of these",
            "not previously shown",
            "remove **[more companies]**",
            "all candidates",
            "case insensitive",
            "do not recommend",
            "top level",
            "do not assume the source folder itself is the company",
            "group or subsidiary",
            "do not recursively search",
            "does not select or bind any input path",
        ):
            self.assertIn(phrase, setup)
        self.assertIn(
            "https://github.com/100xopensource/credit-monitoring-folder-guide",
            guide,
        )
        self.assertIn("packaged", guide.lower())
        self.assertIn("resume", guide.lower())
        self.assertIn("do not clear", guide.lower())
        self.assertIn("do not automatically open", guide.lower())
        self.assertNotIn("crash course", normalized_welcome)
        self.assertNotIn("getting your folders ready", normalized_welcome)
        self.assertIn("before opening either folder picker", normalized_access)
        self.assertIn("**Company documents:**", access)
        self.assertIn("**Results:**", access)
        self.assertIn("anthropic terms", normalized_access)

        store_normalized = " ".join(store.lower().replace(">", "").split())
        for phrase in (
            "choose documents folder",
            "read folder guide",
            "choose results folder",
            "wait for the analyst's response",
            "do not open the source folder picker in the same turn",
            "pause the current turn",
            "do not automatically retry",
            "do not skip ahead",
            "saved monitoring progress remains available",
            "readable basename",
            "no review has started",
            "no review files have been generated",
        ):
            self.assertIn(phrase, store_normalized)
        self.assertLess(store_normalized.index("render and attach the illustrated guide"),
                        store_normalized.index("choose documents folder"))
        self.assertLess(store_normalized.index("choose documents folder"),
                        store_normalized.index("open the source folder picker"))
        self.assertLess(store_normalized.index("open the source folder picker"),
                        store_normalized.index("choose results folder"))
        self.assertLess(store_normalized.index("readable basename"),
                        store_normalized.index("choose results folder"))
        self.assertLess(store_normalized.index("choose results folder"),
                        store_normalized.index("open the output parent folder picker"))
        self.assertLess(store_normalized.index("open the output parent folder picker"),
                        store_normalized.index("run `store.py`"))
        self.assertLess(store_normalized.index("run `store.py`"),
                        store_normalized.index("post-access acknowledgement"))
        self.assertIn("both valid folders are already reachable", store_normalized)
        run_normalized = " ".join(run.lower().split())
        self.assertIn("route intent, not permission", run_normalized)
        self.assertIn("post-access acknowledgement", run_normalized)
        self.assertIn("readiness", folders.lower())
        self.assertFalse((plugin / "references" / "crash-course.md").exists())
        self.assertFalse((plugin / "skills" / "open-crash-course" / "SKILL.md").exists())

    def test_package_check_does_not_require_built_artifact(self):
        output = ROOT / "credit-monitoring.plugin"
        original = output.read_bytes() if output.exists() else None
        output.unlink(missing_ok=True)
        try:
            check = subprocess.run(
                ["python3", "tools/package.py", "check"],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, check.returncode, check.stderr or check.stdout)
            self.assertFalse(output.exists())
        finally:
            if original is not None:
                output.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
