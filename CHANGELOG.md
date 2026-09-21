# Changelog

Notable changes to Credit Monitoring are recorded here.

## [Unreleased]

## [1.1.0] - 2026-09-21

### Added

- Sample-first onboarding: a completed June 2026 sample (six fictional borrowers — workbook, memo, and portfolio dashboard) viewable with no plugin install or review run, via the new `sample-output/` guide.

### Documentation

- Restored the "Advanced: the portfolio dashboard" README section and its cross-link from the two-outputs paragraph, so this candidate does not regress the walkthrough public `main` merged separately.
- Pinned the sample ZIP download to the `v1.1.0` release it will ship under, instead of `releases/latest`. The `v1.0.0` release contains only `credit-monitoring.plugin` and `SHA256SUMS.txt` — the sample ZIP was never published there, so this link resolves once `v1.1.0` itself is published, not before.

## [1.0.0]

Initial public release.

### Added

- Claude Cowork workflow for organizing borrower reporting and signed loan documents.
- Financial workbook and review-ready monitoring memo outputs.
- Optional portfolio dashboard derived from selected completed reviews.
- Read-only source-folder handling and separate writable output selection.
- Evidence links, missing-information flags, and analyst-review boundaries.
- Application-preserved run manifests with integrity checks before reuse.
- Deterministic plugin packaging and published SHA-256 verification.

### Supported scope

- Claude Cowork is the supported v1 environment.
- Updating a hosted dashboard at the same link is not supported in v1.
- See [Limitations](docs/LIMITATIONS.md) for the complete current scope.

[Unreleased]: https://github.com/100xopensource/100x-credit-monitoring/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/100xopensource/100x-credit-monitoring/releases/tag/v1.1.0
[1.0.0]: https://github.com/100xopensource/100x-credit-monitoring/releases/tag/v1.0.0
