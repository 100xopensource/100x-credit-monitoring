# Runtime prerequisites and recovery

Check the interpreter running the engines, not the analyst's desktop Python. Read
`${CLAUDE_PLUGIN_ROOT}/CLAUDE.md`, then run the check-only probe in that same runtime:

```bash
python "${CLAUDE_PLUGIN_ROOT}/lib/requirements.py"
```

Do this **before model-builder dispatch or workbook assembly**, not only at validation.
Exit 0 means required workbook support is ready; exit 1 stops workbook work; usage errors
exit 2. No readiness state is saved. Recheck when the environment changes. Folder preflight
is separate and keeps its existing verdicts/exit codes. Missing Python requires host/IT or
an already provisioned approved environment before this command can run.

| Capability | Requirement | Unavailable behavior |
|---|---|---|
| Workbook build/read/validate and workbook-backed analysis | `openpyxl==3.1.5` importable in the actual interpreter; the appearance comparison is qualified on this version | Stop before assembly/writes/broad input-error handling; no partial model, financial skip record or output replacement. Missing, broken, incompatible or unverifiable versions get the same provision/retry guidance. |
| Folder preflight, independent store, JSON and HTML operations | Python stdlib; POSIX locking where the store needs it | Continue independent operations. Unavailable workbook comparison stays conservative; dashboard sheets are visibly omitted. |
| PDF reader capability | Optional `pypdf` | The source register extracts text PDF evidence when a working reader is present; a missing or broken reader leaves PDF evidence unresolved ("PDF reader unavailable"). Supported non-PDF work is not blocked. No OCR or invented evidence. |
| Formula-preserving recalculation | Optional working LibreOffice through the owning recalculation integration | Readiness reports the source register's read-only discovery verdict (ready/unavailable) without running recalculation; a runtime without that discovery reports integration pending. Discovery does not prove current values, successful recalculation or fallback. Preserve the last usable output when calculations cannot be refreshed. |
| XML SpreadsheetML named `.xls` | Existing stdlib reader | Use existing format sniffing; do not require a binary Excel reader for XML. |
| True binary `.xls` | An already provided suitable reader (`xlrd`; pandas only if that route uses it) | Ask for an authorized unencrypted `.xlsx` or approved reader; do not feed binary bytes to XML/openpyxl. |
| Encrypted workbook | Already provided `msoffcrypto-tool` where needed | Ask for an authorized unlocked copy or approved tooling; preserve the original. |

Optional does not mean an honest full review is possible without the missing evidence or
current calculations. Recalculation/current-values/fallback/edit-refresh behavior belongs
to its existing feature workflow, not this probe. Until product integration is tested,
do not promise a values-only fallback from readiness alone. Auxiliary PDF commands in
older format guidance are conditional tools, never global prerequisites.

## Required dependency recovery

Say plainly: “Spreadsheet support is unavailable in this session, so I cannot build or
verify the workbook. Ask your administrator to explicitly provision openpyxl 3.1.5
in this session's Python using an approved package source/environment, then retry.”
State that existing results are unchanged only when that is true of the work already done.
Keep raw interpreter paths in technical diagnostics, not analyst-facing paraphrases.

**Do not install** packages, Python or tools, invoke an installer, download, or assume
network access. Explicit provisioning is a user/admin action; unavailable offline wheels,
permissions or tooling mean seek IT support or stop. Once provisioned, retry the probe in
the same interpreter and resume existing validation/approval gates. Do not replace missing
figures with zeros, disable validators or bypass pending edits/accepted history.

Local technical evaluation targets provisioned POSIX Python 3.12. Native Windows Python
is not a supported store route. Cowork on macOS and Windows uses its host-managed runtime;
those hosts, marketplace installs and other Python versions need separate qualification.
