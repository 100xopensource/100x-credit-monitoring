---
name: portfolio-artifact
description: >
  Create, reopen, or check the private portfolio dashboard built from selected monitoring results.
  Same-link updates are deferred. Triggers: "create my portfolio dashboard", "update the dashboard",
  "open the portfolio dashboard", "is it current", "show me how to share the dashboard".
metadata:
  version: "0.1.0"
---

# Portfolio Artifact

Read `../../CLAUDE.md` before anything else; it holds the rules shared by every skill.

Guide the analyst from verified local monitoring pages to one private Claude artifact. Build and
check locally before asking. Never upload or share automatically.

## Boundaries

- `refresh-dashboard` owns the local portfolio pages. This skill never recalculates a figure.
- New artifacts are private. Nothing will be shared automatically.
- How the dashboard is created, kept current and updated depends on the runtime:
  `../../references/runtime/<runtime>/dashboard.md`.
- Speak in the plain language required by `../../references/asking.md`. Do not narrate
  discovery commands or show paths, JSON, hashes, metadata, tool names, or platform internals.

## Open, sharing help, and Update requests

Handle these direct requests before looking for the local monitoring folder.

For Open or sharing help, use a reachable complete saved identity when available. Otherwise, list accessible artifacts with the exact title **Portfolio Monitor**; if none match, list accessible artifacts with the legacy exact title **Credit Monitoring Portfolio** (used before this product was renamed). Open the sole match found under either title; if several match, ask the analyst to choose. If none match either title, explain that no accessible dashboard was found. This works without the local monitoring folder. Open makes no platform mutation. Sharing help
points to Claude's Share control but never changes permissions. Do not persist a listed artifact or
claim that it is current.

For a direct Update request, follow **An Update request** in
`../../references/runtime/<runtime>/dashboard.md`.

## Find the local portfolio

Create and "is it current?" require the explicitly connected source and store roots handed off by
`run-monitor`. Check them through `../../references/runtime/<runtime>/files.md`, which says what to
do when either root is unavailable. Do not search the filesystem or infer roots from chat history.

Verify the existing store with the same bindings used by `run-monitor`:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/lib/fleet.py" context \
  --fleet "<store root>/credit-monitoring-registry.json" \
  --source-root "<source root>" --store-root "<store root>"
```

If no registry is reachable, explain that creating or checking a portfolio dashboard needs at least
one saved monitoring review. Offer **[Run credit monitoring]** / **[Not now]** as a menu.

Use these local files:

- pages: `<store>/artifacts/portfolio/Artifact`;
- hosted identity: `<store>/artifacts/portfolio/hosted-artifact.json`;
- candidate: `<store>/artifacts/portfolio/Artifact/.dashboard/credit-monitoring-portfolio.baked.html`.

## Check and build locally

Follow `refresh-dashboard` to check staleness and refresh only what changed. Always finish with:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/skills/refresh-dashboard/references/build_artifact.py" \
  --out "<store root>/artifacts/portfolio/Artifact" --check
```

Stop if the check reports a problem. Preserve the prior valid candidate. Then build the self-contained
candidate:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/skills/portfolio-artifact/references/hosted_artifact.py" build \
  --artifact "<store root>/artifacts/portfolio/Artifact" \
  --template "${CLAUDE_PLUGIN_ROOT}/skills/refresh-dashboard/references/artifact-template.html"
```

Use the returned borrower lists to explain who will appear and who has no selected review yet. Then
follow `../../references/runtime/<runtime>/dashboard.md` from **The safe next action** through
**Finish**.
