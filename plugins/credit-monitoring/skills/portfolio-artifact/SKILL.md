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

Guide the analyst from verified local monitoring pages to one private Claude artifact. Build and
check locally before asking. Never upload or share automatically.

## Boundaries

- `refresh-dashboard` owns the local portfolio pages. This skill never recalculates a figure.
- The local monitoring store is authoritative. Cloud synchronization and connectors are out of scope.
- New artifacts are private. Nothing will be shared automatically.
- Same-link updates are not available in this version. Never rewrite a completed hosted hash.
- Never use legacy artifact registration calls or a connector bridge.
- The draft preview and prior-period pages `refresh-dashboard` writes are local enumerations of what
  the store's own records already prove is safe to show. A rebuilt hosted dashboard bakes them in too,
  each opened through the same internal reader as the accepted result rather than a separate link.
  This adds no second publisher or live update path; a hosted dashboard still only ever reflects what
  was last created here, on request.
- Speak in the plain language required by `${CLAUDE_PLUGIN_ROOT}/references/asking.md`. Do not narrate
  discovery commands or show paths, JSON, hashes, metadata, tool names, or platform internals.

## Open, sharing help, and Update requests

Handle these direct requests before looking for the local monitoring folder.

For Open or sharing help, use a reachable complete saved identity when available. Otherwise, list accessible artifacts with the exact title **Portfolio Monitor**; if none match, list accessible artifacts with the legacy exact title **Credit Monitoring Portfolio** (used before this product was renamed). Open the sole match found under either title; if several match, ask the analyst to choose. If none match either title, explain that no accessible dashboard was found. This works without the local monitoring folder. Open makes no platform mutation. Sharing help
points to Claude's Share control but never changes permissions. Do not persist a listed artifact or
claim that it is current.

For a direct Update request, explain plainly that same-link updates are not available in this version.
Offer **[Open the dashboard]** / **[Done]**. Do not rebuild, publish, create a replacement, or change
hosted identity.

## Find the local portfolio

Create and "is it current?" require the explicitly connected source and store roots handed off by
`run-monitor`. Check them through `connecting-folders.md`. If either root is unavailable, follow
**store-setup** to reconnect both without asking the user to type a path. Do not search the filesystem
or infer roots from chat history.

Verify the existing store with the same bindings used by `run-monitor`:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/lib/fleet.py" context \
  --fleet "<store root>/credit-monitoring-registry.json" \
  --source-root "<source root>" --store-root "<store root>"
```

If no registry is reachable, explain that creating or checking a portfolio dashboard needs at least
one saved monitoring review. Offer **[Run credit monitoring]** / **[Not now]** through
`AskUserQuestion`.

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

Use the returned borrower lists to explain who will appear and who has no selected review yet. Read
the safe next action:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/skills/portfolio-artifact/references/hosted_artifact.py" status \
  --state "<store root>/artifacts/portfolio/hosted-artifact.json" \
  --candidate-sha256 "<candidate sha256>"
```

- `create` — no hosted artifact is known.
- `current` — make no platform call; say it is current and offer to open the saved URL.
- `stale` — make no platform call and do not change the saved hash. Explain that newer local results
  exist but same-link updates are not available in this version. Offer to open the existing dashboard.
- `recover` — make no platform call and never create a duplicate. List accessible exact-title
  artifacts under **Portfolio Monitor**, then under the legacy title **Credit Monitoring Portfolio**
  if none match, and offer **[Open this dashboard]** / **[Cancel]**. Do not adopt or rewrite identity.

## Preflight before asking to Create

Check that the current artifact capability supports private creation and returns an artifact ID and
HTTPS URL. Do not create during preflight.

List accessible artifacts with the exact title **Portfolio Monitor**, then with the legacy exact title
**Credit Monitoring Portfolio**, before asking to create another. If any match either title, offer to
open the chosen artifact instead of creating or recording a duplicate. Only no match under either
title permits Create.

If the capability is unavailable, keep the validated candidate, offer it to the analyst, and explain
the one native Claude Artifacts import step. Do not fall back to legacy artifact calls and do not fail the
completed monitoring run.

## Explain, then ask permission

Say which borrowers and periods will appear, name unavailable borrowers, and say:

> I’ll create a new private portfolio dashboard in Claude. Nothing will be shared automatically.

Call `AskUserQuestion` with:

1. **Create the private dashboard — recommended**
2. **Review the included borrowers**
3. **Cancel**

Cancel makes no platform call and changes no hosted identity.

## Create privately

Before the platform call, record pending creation:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python "${CLAUDE_PLUGIN_ROOT}/skills/portfolio-artifact/references/hosted_artifact.py" prepare-create \
  --state "<store root>/artifacts/portfolio/hosted-artifact.json" \
  --title "Portfolio Monitor" --candidate-sha256 "<candidate sha256>"
```

Use the current Claude artifact capability to create one private artifact from the exact candidate.
Pass no connector capability and no attachment. Verify the returned ID and HTTPS URL, then save them
with the helper's `record` command.

If creation succeeds but recording fails, show the returned link and stop. The pending record blocks
a duplicate Create. Retry the same matching `record` only when the returned Create result remains
available; otherwise open the accessible artifact without adopting it.

## Finish

After verified creation, call `AskUserQuestion` with:

1. **Open the dashboard**
2. **Show me how to share**
3. **Done**

Open the saved HTTPS URL. Sharing help points to Claude's Share control and explains that the analyst
chooses access there. Do not change permissions yourself.
