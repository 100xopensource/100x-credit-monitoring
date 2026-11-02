# The portfolio dashboard in Cowork

In Cowork the dashboard is baked: a created artifact is a self-contained copy of the local portfolio
pages as they stood when it was created.

## What holds in Cowork

- The local monitoring store is authoritative. Cloud synchronization and connectors are out of scope.
- Same-link updates are not available in this version. Never rewrite a completed hosted hash.
- Never use legacy artifact registration calls or a connector bridge.
- The draft preview and prior-period pages `refresh-dashboard` writes are local enumerations of what
  the store's own records already prove is safe to show. A rebuilt hosted dashboard bakes them in too,
  each opened through the same internal reader as the accepted result rather than a separate link.
  This adds no second publisher or live update path; a hosted dashboard still only ever reflects what
  was last created here, on request.
- File synchronization is outside `refresh-dashboard`. A local folder may be synced by the user's own
  tool.
- Cowork registration is a separate step. `refresh-dashboard` writes and validates the files Cowork
  will read. When reporting a refresh, mention that sync and Cowork registration remain separate.

## An Update request

For a direct Update request, explain plainly that same-link updates are not available in this version.
Offer **[Open the dashboard]** / **[Done]**. Do not rebuild, publish, create a replacement, or change
hosted identity.

## The safe next action

After the local check and candidate build in `portfolio-artifact`, read the safe next action:

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
