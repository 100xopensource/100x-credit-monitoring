# Security and privacy

## Processing boundary

Credit Monitoring is a **local-file workflow**, not a local-only processing system.

- The plugin reads only folders the user connects through Claude Cowork.
- Borrower source folders are treated as read only.
- Results and monitoring state are written beneath the results folder the user selects.
- Content sent to or processed by Claude is governed by the user's Anthropic plan, terms, privacy controls, and retention settings.
- The plugin includes no separate account, API key, telemetry service, database, or cloud connector.

Review Anthropic's applicable terms and organizational settings before processing confidential borrower information.

## Hosted portfolio dashboard

A hosted Claude dashboard is separate from local file processing. The plugin validates a local candidate, identifies the borrowers included, and asks permission before private creation. It does not share the dashboard automatically. Sharing is controlled through Claude's own controls.

Same-link updates are unavailable in v1. The plugin does not silently mutate a completed hosted artifact or replace its saved identity.

## Data-handling safeguards

The runtime is designed to:

- avoid writes to borrower source folders;
- refuse unsafe overlap between source and output folders;
- persist portable logical references instead of machine-specific host paths;
- preserve completed selected runs through the application and verify their manifest integrity before reuse;
- reject malformed or incomplete output contracts;
- leave unknown existing output data unchanged rather than deleting or overwriting it.

Completed runs are ordinary writable files, not filesystem-immutable. Users, sync clients, or other software can alter or delete them; a later manifest check can detect alteration but cannot prevent or undo it.

These safeguards do not remove the analyst's obligation to select appropriate documents, permissions, and storage locations.

## Reporting a vulnerability

Do not include borrower files, confidential deal terms, credentials, or personal data in a public issue. Follow [`SECURITY.md`](../SECURITY.md) for the current private reporting route.
