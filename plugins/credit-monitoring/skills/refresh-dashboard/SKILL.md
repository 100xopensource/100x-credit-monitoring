---
name: refresh-dashboard
description: >
  Build or refresh the local portfolio pages from verified published monitoring runs. Use this
  after selecting a monitoring run, when the local dashboard shows an old month, or when asked to
  refresh its saved pages. Hosted create and update requests belong to portfolio-artifact.
metadata:
  version: "0.1.0"
---

# Refresh the portfolio artifact

Read `../../CLAUDE.md` before anything else; it holds the rules shared by every skill.

The dashboard reads one local folder: `index.json` and the pages it names. Each run's pages sit in
their own folder under `views/`, and neither a new run nor a refresh changes an older run's pages. Accepted
figures come from verified runs selected in `published.json`; separately labelled previews come
from verified visible drafts. This skill computes no monitoring result.

## Boundaries

- Artifact pages are derived views, not additional person-facing monitoring outputs.
- A local draft preview is allowed, but must never replace accepted figures. Refresh only on request;
  local generation is neither acceptance nor proof of a hosted update.
- What else holds for the dashboard in this runtime, including sync and registration, is in
  `../../references/runtime/<runtime>/dashboard.md`.

## Accepted, draft, and prior periods

Each borrower's row always names the accepted selection. When that borrower's currently visible
result is a draft nobody has accepted yet, the builder writes it as a separately labelled preview
(its own memo/deal/workbook pages) instead of folding it into the accepted row. A draft never
replaces the accepted pages, and a first-ever draft with no accepted result yet still gets its
preview.

The builder also writes one set of pages per OTHER accepted period on record for the borrower, each
built and verified from that period's own preserved run -- never the newest workbook or memo. A
discarded draft is never selected, previewed, or listed: discarding does not touch the accepted
record. If any named period's saved run no longer verifies, the whole build stops rather than
silently dropping that one link or picking a different run by folder order.

This aligns the local pages with the store's own accepted/draft/history records; it is a local
enumeration of what is safely on record, not a full return-journey browsing experience.

## Output folder

Use `<credit-monitoring-output>/artifacts/portfolio/Artifact`. The final folder name is `Artifact`
because the dashboard and its connector fallback use that name. Do not guess another store: use the
store already selected by `run-monitor`.

## Refresh one selected borrower

Pass the borrower key, not its display name:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/refresh-dashboard/references/build_artifact.py" \
  --store "<credit-monitoring-output folder>" \
  --out "<credit-monitoring-output folder>/artifacts/portfolio/Artifact" \
  --only "<borrower key>" --keep-published
```

On the first build, the builder creates the whole portfolio rather than a misleading book of one.
Later runs rewrite only the selected borrower and preserve the other rows.

Copy the stable dashboard shell beside the pages:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/refresh-dashboard/references/publish_template.py" \
  --out "<credit-monitoring-output folder>/artifacts/portfolio/Artifact"
```

Then check every index reference:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/refresh-dashboard/references/build_artifact.py" \
  --out "<credit-monitoring-output folder>/artifacts/portfolio/Artifact" --check
```

Stop if build or check reports a problem. Do not repair generated pages by hand.

## Refresh on request

On an explicit refresh request, run the whole build without `--only`, publish the local shell, and
run `--check`. Do not use an empty `--stale` result alone to skip rebuilding: draft/history changes
and missing derived pages also need regeneration. Stop on a corrupt or missing saved run; do not
choose a different accepted result or edit state to hide the error. Existing local pages after a
failed build are not verified current. No hosted upload is performed.

## What to report

Say which borrower pages changed and name every flagged borrower. Say “updated the local portfolio
artifact,” not “published” or “deployed,” and add what `../../references/runtime/<runtime>/dashboard.md`
says a refresh report mentions.
