# How it works

Credit Monitoring combines explicit workflow instructions with deterministic Python utilities. Claude performs document interpretation and drafting; the runtime controls storage boundaries, validation, integrity-checked run selection, and derived dashboard assembly.

## From evidence to outputs

1. **Connect folders.** The analyst selects a read-only borrower source and a separate writable results folder.
2. **Configure the borrower.** The plugin records document locations, loan definitions, requested monitoring modules, and decisions that cannot safely be inferred.
3. **Build the workbook.** Source statements and schedules are assembled into a monthly financial history with traceable supporting information.
4. **Run monitoring.** The plugin reviews performance, liquidity, covenant compliance, revenue quality, working capital, collateral, and other enabled modules.
5. **Render the memo.** The memo presents evidence, calculations, risks, limitations, and missing information for analyst review.
6. **Preview and confirm.** The application preserves a completed draft and records a manifest for integrity checks. The analyst accepts or discards that exact draft; newer edits block replacement. Discard retains history and restores accepted working files; the first review cannot be discarded because there is no accepted result to restore.
7. **Derive the portfolio view.** The optional dashboard uses accepted results, with separate visible-draft and prior-accepted-period views. It does not calculate a new credit result.

## Responsibilities

### Deterministic runtime

- prepares the output store without modifying the source;
- validates paths and output structures;
- preserves completed runs;
- checks workbook and memo contracts;
- assembles local portfolio pages from selected runs;
- blocks unsupported hosted-dashboard mutations.

### Claude

- reads and interprets borrower documents;
- asks for decisions and missing context;
- uses the runtime to build and validate outputs;
- explains findings and uncertainties to the analyst.

### Analyst

- verifies source selection and borrower-specific setup;
- reviews formulas, definitions, assumptions, exceptions, and conclusions;
- approves writes and optional hosted-dashboard creation;
- remains responsible for credit judgments and actions.

## Storage model

The source folder is read only. The results folder contains `credit-monitoring-output`, including borrower configuration, work-in-progress files, application-preserved completed runs, current-result selection, and derived portfolio pages. Persisted references use portable source/store-relative forms rather than machine-specific absolute paths.

New-format stores only are supported; older stores are not converted. Each borrower has a `.record/published.json` accepted-result pointer, separate from the visible working copy. This pointer is not a hosted-upload receipt. Local locking serializes writes on one computer, not across cloud-synced computers. Operations use ordered single-file writes, without a transaction journal or multi-file atomicity. After an interruption, resolve the reported permission or I/O problem and retry the same command for the same run. Saved-run verification and existing file hashes distinguish partial copies from newer edits. Claude can request Cowork deletion permission when removing working files, then retry through the plugin; it must not reconstruct internal records to force completion.

Workbook edit intake binds reviewed bytes to a new run, which recomputes analysis and memo before confirmation. Memo intake is not supported, and unselected edits remain protected.

Completed runs remain ordinary writable files. Users, sync clients, or other software can alter or delete them; the application does not provide filesystem immutability and will reject a run whose manifest integrity check no longer passes.

## Dashboard boundary

The local dashboard is regenerated from verified selected runs. Creating a private hosted Claude artifact is a separate explicit action. Opening an existing hosted artifact does not prove it reflects the latest local selection; checking current status requires access to the source and output store. Same-link hosted updates are deferred.
