# Module spec — Covenant Compliance  (FAN-OUT TARGET)

Engine: `covenant_compliance.py`. Depends on covenant-spec.json + build-monitoring-model. Certified
values come from a monthly transcription of the borrower's compliance certificate (Step 1 below).

## Units — a threshold and the model have to be on the same footing

Models may be kept in thousands or whole dollars, so a threshold of `3000` can mean different amounts unless its unit is explicit. The spec says which, once at the top or per covenant: `threshold_unit`
`"dollars"` (as the agreement states them — write these, and quote the agreement's figures),
`"model_units"` (already converted onto this model's scale), `"unitless"` (a ratio, a percentage, a
number of days). The default is `model_units`, which is what every spec written before the field
means, so an existing deal reads exactly as it did. A ratio is never scaled.

A certified figure off the certificate follows its covenant's declaration, so the certified and model
columns sit on one footing. The engine's own constants are always dollars and always converted — the
$1,000 floor under the certificate tolerance is the one that matters: unconverted on a thousands
model it becomes $1,000,000, and a certificate a million dollars out reports as agreeing. The
borrower's unit comes from `model.scale` + `model.currency` in borrower-config (required), and the
output states it on its envelope.

## Purpose
Compute each financial covenant for every month from its first test date and report BOTH sides —
the model reconstruction and the borrower-certified actual — each with pass/at-risk/breach
status and headroom. Calculation is deterministic and auditable; the same inputs reproduce the
same output.

## Inputs (paths from borrower-config.json)
- model_output_path (IS/BS/CF workbook — incl. the pinned EBITDA bridge row)
- covenant_spec_path (covenant-spec.json — definitions WITH explicit flow/spot basis, thresholds/
  schedules, windows, test dates, cure rights, cert_ref)
- the period's compliance certificate — when `paths.compliance_certificate_folder` is configured,
  search it recursively first; if no period certificate is found or readable there, search
  `paths.reporting_packages_folder` as the fallback (skip the duplicate search when both resolve to
  the same folder). Folder and file names vary by deal, so look rather than assume a convention.
  Transcribed, never computed from
- IMPORTANT: use covenant-spec EBITDA/Total Debt/Liquidity definitions; do not invent your own.

## Method — the driving agent does TWO things, in order

### Step 1 — Transcribe the certificate (agent work, transcription only)
Open the period's compliance certificate. Guided by each covenant's `cert_ref`, copy the
borrower's OWN stated actual per covenant into a certified-values file in the monitor output
folder (`_monitor/certified-values-<YYYY-MM>.json`):

```json
{ "source": "<certificate filename>", "period": "YYYY-MM",
  "values": { "max_total_leverage": { "2026-05-31": 3.21 },
              "min_liquidity": { "2026-05-31":
                  { "value": 1250000, "page": 2,
                    "quote": "Unrestricted Cash ................ $1,250,000" } } },
  "transcription_notes": ["what you had to judge while reading the document"],
  "action_items": ["request a June-dated certificate from the borrower"] }
```

A value is the bare number, or the number with the page and the wording you read it from — both are
valid transcription, and the located form is what lets someone check a certified figure without
reopening the certificate. Record the page and quote for each figure you transcribe off a document.

`transcription_notes` and `action_items` are carried into the archived output and rendered into
the note beside it, and the orchestrator puts every action item in the memo. Put anything a person
must DO in `action_items` rather than only describing it in a note — a run once found that a
borrower's June certificate restated May's figures and asked the credit team to chase a corrected
one, and the request reached nobody.

Write that file with the Write tool (its content IS JSON, so `true` / `false` / `null` are JSON), then
`json.load` it back from its final path before running the engine.

Rules: copy numbers exactly as stated (transcription, NOT computation — do not re-derive,
round, or "fix" them); one entry per covenant the certificate reports, keyed by test date;
if the certificate is missing or a covenant is not reported on it, omit the entry — the
engine reports that side as not delivered, which is itself a finding. Never use the
certificate as a source of statement figures.

### Step 2 — Run the engine (engine computes everything)
```
python covenant_compliance.py --borrower-config <path> \
    --certified <_monitor/certified-values-YYYY-MM.json> --out <_monitor/_archive/covenant-compliance-YYYY-MM.json>
```
The engine: resolves each covenant's inputs per the spec definitions on their EXPLICIT basis
(flow inputs aggregate over the covenant window; spot inputs always read the test month),
resolves the applicable threshold per test date (schedules honored), evaluates model AND
certified sides with the shared at-risk band, computes the delta with a primitive-aware
tolerance, and emits exceptions for breaches, at-risk tests, basis warnings, certified-vs-model
divergences beyond tolerance, and any pass/fail disagreement between the two sides.

## Output contract (return ONLY the engine's JSON)
`results` carries ONE ROW PER COVENANT PER MONTH-END from its `first_test_date`. Each row holds the
model side (`required`, `actual`, `headroom_abs`, `headroom_pct`, `status`, `threshold_effective`), the
certified side (`cert_value`, `cert_status`, `cert_headroom_*`) and the reconciliation between them
(`delta_model_vs_cert`, `cert_agrees`) — the memo shows both sides and the disagreement is a finding in
its own right. On a disagreement, the row's `notes` also carries each side's per-component windowed
values (every model row a formula side sums, with its own figure), and the memo's delta note attributes
the gap to the component that carries it — never to a guessed cause the components contradict. `exceptions` carries the breach / at_risk / basis_inferred / double_window_risk /
input_coverage_gap / input_partial_window / cert_model_status_divergence entries. The engine defines
the exact shape; read it there.

**Either/or covenants resolve in the engine — report the group, not the legs.** An agreement that
tests two covenants as alternatives ("maintain X OR Y") is ONE covenant with two legs. Alongside the
leg rows the engine emits a group row (`kind: "either_or_group"`) holding the group's `status`, its
`members`, each leg's status in `legs`, and `headroom_pct` taken from whichever leg holds the most
room (named in `headroom_from_leg`). The GROUP's status is the covenant's compliance; the leg rows
show how it got there. Every leg row and leg exception carries `group_status`, so a leg that missed
its own level while another leg held reads as a leg miss — its exception carries `group_note` saying
so — and is written up as a leg miss, not as a covenant breach. A group held up only by a leg that is
itself at risk reports `status: "pass"` with `satisfied_only_by_at_risk_leg: true`: report the pass
and the warning together.

**Two statuses mean "no answer", and each is a finding.** A covenant past its first test date that
cannot be computed gets a **row** saying so, with null `required`/`actual` and a `reason`; the
covenant's `not_evaluable` warning lists every affected date in `test_dates`. Five reasons:

| `reason` | what it means |
| --- | --- |
| `gap` | the threshold schedule marks this period as not tested |
| `insufficient_history` | the trailing window reaches back past the model's first month, and no deemed schedule covers the months before it |
| `input_blank` | the bound model row is EMPTY for this period — no value to test, which is not a value of zero |
| `denominator_zero` | the ratio's denominator is a real, present zero here, so the ratio is undefined |
| `input_unresolved` | the spec's compute binding names a model line the workbook does not have |

A `not_evaluable` row still carries the CERTIFIED side when the certificate reports that covenant at
that date (`cert_value`, `cert_status`, `cert_headroom_*`), with `cert_agrees` and
`delta_model_vs_cert` null because there is no model figure to compare. These are the months a reader
most needs the borrower's own number, since the model side has nothing to say — report both: the model
could not test it, and the certificate claims this. The certificate never changes the row's `status`.

`indeterminate` on a group row means no leg is satisfied and at least one could not be evaluated, so
compliance at that date is unknown — including where one leg failed and the leg that might have
satisfied the group was never tested. `not_evaluable` on a group row is the narrower case where the
threshold schedule takes EVERY leg out of testing that period: the group was not tested, which is a
different statement from unknown, and its `reason` (`gap` or `not_yet_effective`) says so. Report it as
a period the agreement does not test, not as a gap in the evidence.

Report every `indeterminate` date as unknown, and say which reason:
`insufficient_history` resolves itself as the model grows, while `input_blank`, `input_unresolved` and a
persistent `denominator_zero` mean the spec or the model needs fixing — that difference is what the
reader acts on. A month nobody could compute reads as a quiet, compliant month unless you say otherwise.

**Fixed Charges mean contractual debt service, and the engine says when the model is only a proxy.**
A coverage covenant's denominator (FCCR, DSCR, interest coverage) is the facility's own cash interest
and scheduled amortization. Those figures reach this module through model rows the build populates
from the borrower's generated debt-service schedule (covenant-setup writes `debt-service-spec.json`
and generates the schedule; assembling-rules populates the covenant-support rows from it). Two things
to carry into the memo:
- A **`fixed_charges_ledger_proxy`** warning means no schedule has been built for this borrower, so
  the coverage covenant's model side reads whatever the ledger put in the bound rows — booked interest
  expense sweeps in pre-close and non-facility borrowings, and no statement line carries scheduled
  amortization. The
  verdict still computes and both columns still show; the memo says **"fixed charges on ledger proxy —
  debt-service schedule not yet built"** rather than publishing the proxy as the model read. The fix
  is at the data layer: covenant-setup's facility-terms step, then a model refresh.
- On a schedule-fed covenant, a certified-vs-model delta beyond tolerance is a **reconciliation ask**:
  the model side is now the contract's own arithmetic, so a disagreement means the borrower's
  certificate and the executed agreement's terms diverge somewhere nameable — a day-count stub read,
  a grid band, a payment the certificate accrues that the schedule does not. Put the ask in
  `action_items` with the component the `notes` decomposition points at; never silently substitute
  either side. While transcribing the certificate, also check the grid metric it certifies against the
  band the schedule's spec last observed (`observations.grid_state`) — a crossed band means the
  schedule needs its observation appended and regenerated at the data layer, and that is an action
  item too.

**Months the agreement DEEMS are the agreement's figures, and they govern.** Where a definition carries a
`deemed_schedule` (covenant-setup step 3a — an acquisition / LBO financing fixing EBITDA for the pre-close
run of months), the engine reads those months from the schedule instead of the model, including months that
predate the model entirely, which is what lets a trailing-window test compute at its early test dates at
all. Two things to carry into the memo:
- Each period whose window leans on the schedule carries a dated `deemed_window_mix` warning saying how
  many of its window months are deemed and when the run clears the window. Say it in the covenant write-up:
  a ratio resting on stipulated months is a different fact from one computed off delivered statements, and
  it usually **weakens as the run rolls off**, one month at a time, since a deemed figure is the number the
  parties agreed to make the metrics work.
- A `deemed_model_disagreement` warning means the workbook's own row for a deemed month is not the deemed
  amount. The covenant is tested on the agreement's figure; the model's row still feeds every other read of
  that month, so this is a build to fix (`assembling-rules.md`), not a covenant finding.

**An empty cell is not a balance of zero, and the engine keeps them apart.** A blank stays absent all
the way through: a spot read of a blank month, or a flow window blank end to end, is `input_blank`.
Two companion exceptions carry what the reader needs to fix it:

- `input_coverage_gap` — the bound row is empty for part of the model's months. It names the empty span
  and any same-sheet row that appears to be a renamed continuation, so the binding can be repointed.
- `input_partial_window` — a flow window that is only partly blank still sums and the verdict STANDS,
  but the total covers fewer months than the window implies. Report the figure as understated; for a
  coverage ratio a blank in the denominator flatters the result.

Numbers are numeric (no display strings) — formatting for the memo happens at synthesis, not here.
`period` is the run's single ISO `YYYY-MM` stamp; the full month axis lives in `model_month_ends`.
Every row answers both provenance questions without re-running the engine: `source_ref` (which
file/sheet/cells each input came from — `file` indexes into `source_files`, spec and certificate refs
carry `section`) and `calc` (each side's rows, window, months, and the test applied). **Gate the output
with `validate_output.py <out.json>` before archiving.**

## Engine vs driving agent (hard boundary)
The ENGINE computes everything in this spec. The driving agent transcribes the certificate,
runs the engine, reports failures, and may add NOTHING to the output JSON — no custom keys, no
hand-recomputed values, no covenant math of its own. If the engine cannot produce a section,
report the gap. Engine bugs found mid-run are fixed IN THE ENGINE, tested, and synced back via the
plugin repo — never patched in output.

**Copy the engine file BYTE-FOR-BYTE to the working directory (filesystem copy, or chunked reads with
offset continuation until EOF), verify the copy (byte count or checksum against the source), and run
THAT file. A reconstructed engine is not the engine — if a complete copy cannot be obtained, STOP and
report the gap.**

## Source tier & discipline
audited > field exam > draft/management financials > compliance cert. The certificate is a
transcribed CROSS-CHECK column, never the source of statement figures. Both sides are always
shown side by side; neither silently replaces the other. Surface & flag; do NOT make a credit
recommendation.

**Our reconstruction governs the verdict.** Report the gap and the component that carries it; never
write that the borrower's basis governs — `validate_memo.py` refuses that sentence. A certified
figure that reads the defined term better than ours is a model binding to repoint at the data layer,
not a basis to switch. A certified-vs-model disagreement is reported for reconciliation; it never
silently changes which basis governs the verdict.

## Definition of Done
- Every financial covenant computed from the model using spec definitions on their explicit
  basis; threshold schedules honored per test date; certified side transcribed (or its absence
  flagged); both statuses + headrooms emitted with the delta and tolerance; reference-only
  entries skipped cleanly; windowing handled (insufficient history → a dated `not_evaluable` row,
  never a guess and never an absence); exceptions flagged. Numbers reproduce on re-run.
- Every covenant past its first test date has a row at every test date — computed, or
  `not_evaluable` with a reason. A missing row is a bug, not a silent "not applicable".
- No verdict rests on an empty cell. A blank input reads as `input_blank`, never as zero; every
  `input_coverage_gap` and `input_partial_window` is named in the memo with the row it concerns, since
  each one means a binding needs repointing before the next run.
- Each either/or group reported by its resolved status, with a leg that missed its own level while
  the group held written up as a leg miss. Every `not_evaluable` and `indeterminate` date named in
  the memo as unknown, never left out.
