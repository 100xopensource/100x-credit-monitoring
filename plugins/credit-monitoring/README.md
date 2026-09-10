# Credit Monitoring Plugin

**AI-assisted private-credit monitoring for credit professionals using Claude Cowork.**

This installed package turns borrower reporting and loan documents into a structured financial workbook and a review-ready monitoring memo. It helps organize evidence and analysis; the credit analyst remains responsible for reviewing every result and making every credit decision.

## Run it

1. Start a new chat with the Credit Monitoring plugin enabled and say **“credit monitoring.”**
2. To go straight to one borrower, say **“Run credit monitoring for [borrower name].”**
3. Choose the read-only borrower source folder, then a separate writable results folder.

A first setup can take an hour or more for document-heavy borrowers. Claude reports progress and supports pause and resume. For illustrated Local, OneDrive/SharePoint, and Google Drive desktop folder steps, open the packaged [Getting your folders ready](references/getting-your-folders-ready.html) guide.

## Outputs

Each review produces exactly:

1. one financial workbook with monthly history, supporting schedules, and monitoring calculations; and
2. one monitoring memo covering performance, liquidity, covenants, risks, evidence, and missing information.

The optional portfolio dashboard is a separate combined view of reviews you have already finished, not a third output. Private dashboard creation and opening are supported in v1; updating a hosted dashboard at the same link is not.

## Drafts, edits and saved results

A finished review is a draft. Inspect its workbook and memo, then accept or discard that exact result. Discard preserves its history and restores accepted working files; the first review cannot be discarded because there is no accepted result to restore. Newer manual edits block replacement.

Ask Claude to incorporate workbook edits into a new draft. It checks the reviewed bytes, reruns analysis and renders a new memo before confirmation. Memo edit intake is not supported; preserve those edits and resolve them before continuing.

Only new-format stores are supported; older stores are left untouched, not converted. Local dashboard refresh rebuilds views from verified saved runs. Drafts and prior accepted periods are separately labelled and are included when creating a hosted dashboard. Local refresh does not update an existing hosted dashboard. Corrupt saved outputs require recovery from trustworthy copies, not an automatic change of accepted result. Writes are ordered, not a multi-file transaction; no transaction journal is created. After a filesystem interruption, Claude resolves the reported permission problem and retries the same operation against its saved run, without overwriting newer edits.

## Privacy and responsibility

This is a **local-file workflow**, not a claim of local-only processing. The plugin reads user-selected files and stores results in a user-selected local or synced folder. Document content processed by Claude is handled under the user's Anthropic terms and settings. Creating a hosted Claude dashboard is a separate, explicit action; nothing is shared automatically.

Completed reviews are preserved by the application and checked against a manifest before reuse. They remain ordinary files: users, sync clients, or other software can alter or delete them, after which integrity verification will fail.

The plugin does not replace professional judgment. Verify source evidence, formulas, covenant definitions, exceptions, and conclusions before relying on an output.

Licensed under [Apache-2.0](https://github.com/100xopensource/100x-credit-monitoring/blob/main/LICENSE). Full documentation: <https://github.com/100xopensource/100x-credit-monitoring>.
