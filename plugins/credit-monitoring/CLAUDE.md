# Credit Monitoring runtime

Read the selected skill for its workflow. Start borrower monitoring with `run-monitor`.

- Know the runtime before following any skill: **Know the runtime** in `references/entry-routing.md`.
  `<runtime>` in a path is that runtime's folder name (`cowork` or `claude-tag`); every worker brief
  names the runtime and the plugin folder.

## Runtime prerequisites — check before workbook work

Use an already provisioned runtime. Before model-builder dispatch, workbook assembly,
validation or workbook-backed analysis, run
`python "${CLAUDE_PLUGIN_ROOT}/lib/requirements.py"` with the **same interpreter**
that will run the engines. Read `${CLAUDE_PLUGIN_ROOT}/references/runtime-prerequisites.md`
for capabilities and recovery. This check is read-only; it does not certify financial correctness.

If required support is unavailable, do not dispatch a workbook worker, assemble a partial
model, replace existing results or write a financial skip record. Explain the unavailable
capability and ask for explicit approved provisioning in this session's runtime, then retry
or stop. Do not install packages/Python/tools, run pip/uv setup, download or assume network
access. Do not redirect to desktop Python when the engine runs in a host-managed sandbox.
If Python cannot start (command-not-found), stop before dispatch and request host/IT
provisioning or an existing approved environment; a Python probe cannot fix missing Python.

Local technical verification targets provisioned POSIX Python 3.12. Native Windows Python
is not a supported technical route (store locking requires POSIX); use Cowork's managed
runtime or an approved POSIX environment. Cowork macOS/Windows acceptance requires actual
host testing; technical Python checks do not prove host or marketplace compatibility.
Folder preflight and independent store/JSON/HTML operations do not require openpyxl.

## Engine execution boundary

During an analyst review, run the shipped engines unchanged, directly or from byte-identical copies.
Do not patch engine code, copied engines, validators, or calculated output to turn a skip or failure
into a result. Correct an invocation or verified input mapping only within the approved workflow;
never invent a missing input. Preserve a valid engine skip. If execution or validation fails, use
the documented skip record and explain the unavailable analysis. Code fixes require a separate
engineering change and verification before a new review uses them. Worker briefs must carry this
boundary, and the orchestrator must reject results reported as produced by modified engines.

- Read only source folders the user granted; never modify borrower source documents.
- Keep monitoring state in the configured output store, not session memory.
- Preserve the store's draft, preview and explicit acceptance steps. Do not overwrite accepted history.
- Keep source references with financial figures. Report missing inputs and validation failures; do not invent values.
- Build local outputs by default. Hosted publication requires an explicit user request and approval.
- Use the tenant-neutral configuration and templates shipped with this plugin. Do not assume a client, connector or machine path.
