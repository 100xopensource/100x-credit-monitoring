# Completed sample — June 2026

**See the result before spending time on a review.** This sample contains finished financial workbooks, monitoring memos, and a portfolio dashboard for six fictional borrowers. Viewing needs no Claude account, plugin installation, or review run.

## 1. View sample results

- **[View the live portfolio dashboard](https://100xpartners.ai/credit-monitoring/)** — explore the completed sample directly on the 100x Partners website. No download or review run required.
- **[Download the portfolio dashboard](https://raw.githubusercontent.com/100xopensource/100x-credit-monitoring/main/sample-output/portfolio-monitor.html)** — save it as `portfolio-monitor.html`, then double-click it to open in a browser. If the browser displays HTML text, use **Save Page As** and keep the `.html` extension.
- **[Download the complete sample ZIP](https://github.com/100xopensource/100x-credit-monitoring/releases/download/v1.1.0/credit-monitoring-sample-output-2026-06-v1.zip)** — extract it, then open `sample-output/portfolio-monitor.html`. The ZIP includes the Excel workbooks, HTML memos, and saved working files. This link resolves once `v1.1.0` is published; the `v1.0.0` release shipped only the plugin bundle and `SHA256SUMS.txt`, not this sample.
- Alternatively, use the repository's **Code → Download ZIP**, extract it, and open its `sample-output` folder.

GitHub displays HTML source rather than running the dashboard. Download and open it locally; no server or build step is required. In a downloaded copy, the dashboard is also [here](portfolio-monitor.html).

**Source documents:** download the fictional borrower inputs separately from the [synthetic borrower repository](https://github.com/100xopensource/100x-credit-monitoring-synthetic-dataset). This output ZIP contains the completed reports and their supporting saved records, not the input dataset.

### Individual reports

In the downloaded sample, open the HTML memos in a browser and the workbooks in Excel. On GitHub, use **Download raw file** on an individual file's page.

| Borrower | Monitoring memo | Financial workbook |
|---|---|---|
| Asteron Care Partners | [Memo](outputs/credit-monitoring-output/borrowers/asteron-care-partners/Asteron%20Care%20Partners%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/asteron-care-partners/Asteron%20Care%20Partners%20Monthly%20IS-BS-CF.xlsx) |
| Nemeris Field Systems | [Memo](outputs/credit-monitoring-output/borrowers/nemeris-field-systems/Nemeris%20Field%20Systems%2C%20LLC%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/nemeris-field-systems/Nemeris%20Field%20Systems%20Monthly%20IS-BS-CF.xlsx) |
| Orinth Vale Infrastructure | [Memo](outputs/credit-monitoring-output/borrowers/orinth-vale-infrastructure/Orinth%20Vale%20Infrastructure%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/orinth-vale-infrastructure/Orinth%20Vale%20Infrastructure%20Monthly%20IS-BS-CF.xlsx) |
| Selvara Industrial Services | [Memo](outputs/credit-monitoring-output/borrowers/selvara-industrial-services/Selvara%20Industrial%20Services%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/selvara-industrial-services/Selvara%20Industrial%20Services%20Monthly%20IS-BS-CF.xlsx) |
| Talvorn Technical Services | [Memo](outputs/credit-monitoring-output/borrowers/talvorn-technical-services/Talvorn%20Technical%20Services%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/talvorn-technical-services/Talvorn%20Technical%20Services%20Monthly%20IS-BS-CF.xlsx) |
| Veyrona Decision Systems | [Memo](outputs/credit-monitoring-output/borrowers/veyrona-decision-systems/Veyrona%20Decision%20Systems%20Monitoring%20Memo.html) | [Excel](outputs/credit-monitoring-output/borrowers/veyrona-decision-systems/Veyrona%20Decision%20Systems%20Monthly%20IS-BS-CF.xlsx) |

The dashboard's financial-model view is not an Excel download. Use the workbook links above or the files in the ZIP for the actual `.xlsx` workbooks.

## 2. Continue from the sample

This is a convenient copy of a completed run, **not a guarantee of seamless continuation across plugin versions**. **The currently prepared release plugin rejects this sample during results-folder setup.** Viewing the delivered files and running a fresh review are available; in-plugin continuation of the saved store is not verified or supported by this candidate. Do not remove records or bypass that check.

You can still open the downloaded workbook, memo, or dashboard directly and use those files as reference. If you want the plugin to generate results now, use [Run the sample yourself](#3-run-the-sample-yourself) below.

### Folder map for a compatible plugin

The following is the saved sample's folder map, not a working continuation walkthrough for the current candidate:

1. Keep an untouched copy of the extracted sample, and work in a second copy. Keep the hidden `.record` folders: they contain saved review state.
2. Use the **full six-borrower [source dataset](https://github.com/100xopensource/100x-credit-monitoring-synthetic-dataset)**. This sample was produced from dataset metadata version `v1.0.0`, with 755 source documents. The document contents also match the prepared dataset candidate labelled `v1.1.0`; exact fingerprints are in [sample-manifest.json](sample-manifest.json). A minified sample is not a substitute for these saved file references.
3. The documents root is **`dataset/Current Portfolio`**, containing all six borrower folders. This existing sample's saved references include borrower folder names, so it needs this parent—not just Nemeris' folder.
4. The results parent is **`sample-output/outputs`** in your working copy—not `credit-monitoring-output` itself.

For example:

```text
My sample workspace/
├── source-dataset/
│   └── dataset/
│       └── Current Portfolio/       ← documents selection for this saved sample
│           ├── Asteron Care Partners/
│           ├── Nemeris Field Systems/
│           └── ...
└── sample-output/
    ├── portfolio-monitor.html
    └── outputs/                     ← results selection
        └── credit-monitoring-output/
```

**Why the limitation?** The sample includes a supplementary borrower directory, `borrowers/orinth-vale-infrastructure/_docs/`, which the prepared plugin's folder validator does not accept. At borrower level, the validator permits regular files and the `.record/` and `_model/` directories, but not `_docs/`. The `store-schema.json` and per-borrower `.record/published.json` files are accepted; they are not the cause of this rejection. A direct read-only diagnostic lookup can locate all six archived reviews as **unpublished results**, but that does not bypass the failing folder-setup step or prove that the Cowork workflow can continue. The package preserves the supplied records rather than converting them.

After viewing, ask for the specific next action you want. There is no unfinished June review to resume and no supplied July run. New analysis still takes time; this package does not make the monitoring engine faster. A hosted Claude artifact is not inherited from the author, and the included HTML dashboard does not automatically update.

## 3. Run the sample yourself

To generate your own results instead of using the saved ones:

1. Install the [Credit Monitoring plugin](https://github.com/100xopensource/100x-credit-monitoring#install) in Claude Cowork and download the source dataset above.
2. Start with **one borrower**, for example: **“Run credit monitoring for Nemeris Field Systems.”**
3. For this fresh run, select **`dataset/Current Portfolio/Nemeris Field Systems`** as the documents folder, following the dataset's normal instructions.
4. Select a **separate empty results parent outside the dataset and the downloaded sample**. Do not point a fresh run at the pre-run sample's `outputs` folder.
5. Follow the ordinary setup/review flow. First setup can take an hour or more; keep Cowork open. Generated wording and judgments may differ from this snapshot.

## What is included and what is not

- Snapshot `2026-06-v1`: all six borrowers, June 2026 review period.
- Actual workbooks and memos, the self-contained dashboard, and supporting saved state. Financial results have not been recalculated or relabelled for this distribution.
- Original gaps remain visible: Asteron's aging analysis was skipped; Selvara's budget comparison was skipped; other missing modules and qualifications are shown in the reports. This is a sample, not a claim that every module succeeded.
- All six archived run manifests verify. Their delivered memos match the dashboard. The six working `current` folders lack a memo copy listed in their record-level manifests; those folders have been preserved, not repaired. Use the delivered or archived reports.
- The input evidence and the plugin are separate downloads. This output pack is not included inside the plugin installer.
- Local lock files, a scratch backup, macOS metadata, and the author's hosted-artifact identity were omitted. Saved financial records and historical run contents were preserved.

See [sample-manifest.json](sample-manifest.json) for provenance, checksums, and the bounded verification results. Contents are fictional and for demonstration. [License](LICENSE) · [Notice](NOTICE).
