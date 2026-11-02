# Limitations

## Origin and affiliation

The workflow in this repository is general-purpose private-credit monitoring. It does not represent the investment strategy, credit methodology, underwriting standards or views of any firm, fund, client or investment manager. Nothing here describes how any specific institution makes credit decisions.

Borrower names, financial figures and covenant terms in examples, templates, test fixtures and documentation are fictional.

## Product scope

- This public installation guide supports Claude Cowork. The plugin also contains instructions for administrator-configured Claude Tag channels, which this guide does not offer as a setup. Compatibility with other runtimes is not promised.
- The plugin assists monitoring; it does not approve credits, waive defaults, interpret law, or replace analyst review.
- Results depend on the completeness, consistency, and readability of borrower evidence.
- A first setup can take an hour or more for document-heavy or complex borrowers.

## Financial analysis

- Contract definitions, amendments, certificate conventions, and unusual accounting treatments may require explicit analyst decisions.
- Missing reporting is surfaced as a limitation; the plugin must not invent evidence.
- Workbook formulas, covenant calculations, and memo conclusions require professional review before use.
- The plugin is not legal, accounting, investment, or regulatory advice.

## Workbook edits and saved reviews

- Integrity checks may not detect every change to the relationships between workbook sheets. If a check refuses a file, preserve the affected files and ask for help; do not edit internal records to bypass a refusal.
- If a retry after an interrupted draft discard refuses unchanged files, preserve them and ask for help.
- In Cloud Cowork, approved files are staged and copied back to the results folder, and edits made to connected results while a review is running are not guaranteed to be detected. Avoid editing connected results during a review.
- A native Excel save-and-refresh round trip has not been verified. Keep a separate copy before editing, and check refreshed results before relying on them.
- Workbook refresh accepts only supported edits and requires successful recalculation. A values-only fallback is not guaranteed when LibreOffice is unavailable: formula inputs require an initial successful calculation, and edit refresh stops if recalculation fails.
- Sample Output illustrates an earlier published build. Setup can recognize its saved results, but end-to-end continuation has not been verified; use a fresh results folder for new reviews.

## Portfolio dashboard

- The dashboard is derived from verified selected monitoring runs; it is not an independent credit calculation or a third output.
- Creating a private hosted dashboard is explicit and separate from local file handling.
- Updating a hosted dashboard at the same link is not supported in Cowork.
- Opening a saved hosted dashboard does not establish that it reflects the current local selection. A current-status check requires the source and output store to be connected.

## Compatibility and history

- Migration of monitoring runs created before the public v1 storage contract is unsupported. Start with a fresh v1 output store.
- Files visible in a synced folder must also be readable on the computer; online-only placeholders cannot be analyzed until downloaded by the user's sync tool.
