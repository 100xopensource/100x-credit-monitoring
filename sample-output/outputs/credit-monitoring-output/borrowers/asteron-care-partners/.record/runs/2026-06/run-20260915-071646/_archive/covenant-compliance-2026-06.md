# covenant-compliance — Asteron Care Partners, LLC, 2026-06

Run run-20260915-071646 · 2026-09-15T07:24:05+00:00

## Action items

- Stabilization Milestone M4 ('Deliver FCCR reinstatement readiness analysis', owner CFO, due 2026-09-30) is still PENDING as of the June 2026 package (Successor Reporting/Planning/Stabilization Milestones.xlsx) -- due in about two weeks from today's date. Confirm the analysis is delivered on time and revisit covenant-spec.json's open step7_signoff item on whether the FCCR mechanically reinstates at 1.2000x on 2027-01-01, given the informational ratio is deeply sub-1.0x and has worsened every quarter since suspension (1.0478x Q4-2025; 0.6687x Q1-2026; 0.3682x Q2-2026).

## Notes on what was read off the documents

- min_liquidity's certified actual ($18,817,443.79) is transcribed from the companion 2026-Q2 PM Update.pdf 'LIQUIDITY' line, not from Compliance Certificate.pdf itself -- this quarter's compliance certificate text states only the FCCR calculation and disposition, with no liquidity figure printed on it. Both documents sit in the same Portfolio Monitoring/2026-Q2 folder and are read together as the quarter's compliance-certificate/covenant-calculation exhibit per covenant-spec.json's min_liquidity cert_ref. The engine attaches one upstream document address to every value transcribed into this file (its top-level 'source' field, set here to Compliance Certificate.pdf), so the archived source_ref for the liquidity row's certified side will display the Compliance Certificate.pdf address even though the number itself came from PM Update.pdf in the same folder -- the quote field above carries the correct citation regardless.
- fccr's certified TTM Covenant EBITDA ($6,413,418.70) and TTM fixed charges ($17,417,557.44) are the borrower's own, broader figures (Covenant Calculation.xlsx TTM Detail) -- wider than this spec's model-side EBITDA (Reported EBITDA, no lease add-back) and Fixed_Charges (facility cash interest + scheduled principal only, no lease payments or maintenance capex) definitions per covenant-spec.json's step7_signoff. A certified-vs-model FCCR delta on this basis is expected and documented there, not an error.
- Stabilization Milestones.xlsx (Successor Reporting/Planning) independently confirms Milestone M3 'Maintain minimum liquidity of $10 million' status COMPLETE as of 2026-06-30, consistent with the certified liquidity figure and with the model's own Cash balance for June 2026.
- The FCCR test date resolved for this period (2026-06-30) falls inside the covenant-spec threshold_schedule's SUSPENDED step (effective 2025-12-05, value null) -- the model side is expected to report this covenant as not_evaluable/gap for June 2026, per the module spec's 'Two statuses mean no answer' rule; the certified value above is the informational cross-check the module spec says to carry on that row regardless.

## How this period's figures were prepared

- Company-prepared/unaudited model; EBITDA taken from the model's single pinned EBITDA bridge row (not recomputed). Thresholds and definitions per the standardized covenant-spec; each definition's flow/spot basis is explicit in the spec. cert_* fields are the borrower's own certified actuals transcribed from the compliance certificate -- reported alongside the model reconstruction, never merged with it.

## Flagged this period

- **fccr** (not_evaluable) — insufficient_history
- **fccr** (not_evaluable) — gap
