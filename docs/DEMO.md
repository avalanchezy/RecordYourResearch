# Demo guide

This guide is for showing RecordYourResearch to an audience (a live demo or a short video). Everything below uses **fictional data**; your own `data/` folder is never touched.

## Start the demo

```bash
python run.py --demo --open
```

- Uses a separate folder, `demo-data/`, filled with fictional submissions the first time (via `tools/demo_seed.py`), and listens on port **8766**, so it can run next to your real instance on 8765.
- A **Demo data** badge in the top bar reminds viewers that nothing shown is real.
- To start over, stop the server and delete `demo-data/`.
- The **EN / 中文** button in the top right switches the whole interface, including messages from the server. The choice is remembered in the browser; a browser whose language is not Chinese starts in English.
- Demo mode makes no network requests: the OpenReview sync results shown are pre-seeded.

Pages to paste during the demo are in [`docs/demo/`](demo/):

| File | What it shows |
|---|---|
| `scholarone-author-dashboard.txt` | A ScholarOne Author Dashboard with 3 manuscripts |
| `editorial-manager-folders.txt` | Several Editorial Manager folders pasted at once, 5 manuscripts with different outcomes |
| `elsevier-query-changed-counts.txt` | Elsevier China's status page right after reviewer counts changed (copied text reads `12`, `23`, `45`) |

All three are synthetic; the first two come from the test corpus in `tests/fixtures/syspaste_corpus.json`.

## Five-minute storyboard

| Time | Scene | What the audience sees |
|---|---|---|
| 0:00–0:30 | The problem | A researcher with papers at OpenReview conferences, Elsevier journals and a ScholarOne journal: every system shows status differently, and none of them has an author API. |
| 0:30–1:15 | Overview | Counts by lifecycle stage, alerts (a revision due in 2 days, a paper “with editor” for more than four weeks), and the *since your last visit* panel with an automatically detected change and an OpenReview submission waiting to be imported. |
| 1:15–2:15 | Import by pasting | Paste `scholarone-author-dashboard.txt` into **Import from submission system**. The preview recognizes the system, splits the page into manuscripts, normalizes each raw status (e.g. *Awaiting Reviewer Scores* → Under review) and proposes *create* or *update* per manuscript. Import; the new records appear with their timelines. Repeat with `editorial-manager-folders.txt` to show several folders in one paste. |
| 2:15–3:15 | Noisy extraction | Open *A Survey of Placeholder Methods for Pattern Recognition*, click **Paste official result** and paste `elsevier-query-changed-counts.txt`. The page shows `old → new` with an arrow icon, so the copied text reads `12`, `23`, `45`. The preview reads *completed 1→2, accepted 2→3, invited 4→5*, explains the reading, and would flag it if two readings stayed plausible. |
| 3:15–4:15 | Provenance and lineage | The status timeline: each entry carries its source (manual, OpenReview sync, pasted page), when it happened vs. when it was recorded, and the raw official text. Then the rejected ICME paper and its ICASSP resubmission, linked both ways, with CCF / CAS ranking badges matched offline. |
| 4:15–5:00 | Local-first | Switch to Chinese and back, export the ledger to CSV, and point out that the database, PDFs and credentials (in the OS keyring) never leave the machine. |

## Live demo at the booth

Let visitors drive. Good prompts:

1. *Paste a page from a system you use.* The corpus covers ScholarOne, Editorial Manager, Atypon ReX (IEEE Author Portal, Wiley), Springer Nature Snapp / Research Square, MDPI / Taylor & Francis / Frontiers, and several Chinese journal systems.
2. *Try to confuse the counter parser.* Edit the numbers in `elsevier-query-changed-counts.txt` (for example `12` → `112`) and watch the preview's reading and its ambiguity warning.
3. *Log a status change in the past.* Use **＋ Log a status change** with an earlier time: the entry is placed in the timeline, and the current status only changes if nothing later exists.
4. *Look up a venue* under **Channels → Journal / conference ranking lookup**.
6. *Ask your submission history* (needs the smart assistant). On the overview, ask "Which papers have been under review for more than 3 months?" The answer shows how the question was understood, the rows, and the exact read-only SQL that ran. Then paste the synthetic decision letter from `docs/demo/decision-letter.txt` into a submission's "✉️ Paste editor's letter": rules and model agree on *major revision* and the deadline is checked against the letter before you confirm.
5. *Paste a screenshot instead of text* (needs `requirements-ocr.txt`). Take a screenshot of one of the sample pages opened in a browser and press `Ctrl+V` in the paste box: the image is recognised on the CPU, the text appears in the box for correction, and the same parser preview follows. The confirmed event carries an **OCR** tag with the lowest line confidence.

## Technical points worth stressing

- **One lifecycle model over heterogeneous sources.** OpenReview notes, Elsevier's status API and Chinese status page, and free-text author-center pages from many systems are mapped onto ten normalized stages, while the raw official text is kept next to each event.
- **Extraction from copy-paste, no scraping.** Publishers offer no author API and forbid automated logins, so the user copies the page and the app parses plain text: no DOM, no credentials, no network access. Parsing is tested against 56 synthetic pages (179 manuscripts) from 6 system families.
- **Constraint-based disambiguation.** Glued `old new` counters are split by ranking all readings with domain constraints (completed ≤ accepted ≤ invited, counts rarely decrease, hidden counts show the threshold) and the history of the same manuscript; residual ambiguity is shown to the user instead of being guessed away.
- **Provenance-aware event log.** Every status change is an event with source, observed time and recorded time; late information is back-filled without overwriting newer state, and repeated imports are idempotent.
- **Rules first, a small local model as a checked assistant.** An optional on-device model (Qwen3.5-0.8B on llama.cpp, installed from the UI, about 0.9 GB) reads editors' letters, suggests stages for unknown status words, and maps natural-language questions onto a fixed set of parameterised SQL templates. Its outputs are schema-constrained, every date, manuscript number and quote must be found in the source text, rules and model cross-check each other, confirmed suggestions are distilled into rules (so the model is consulted less over time), and the SQL that answers a question is always shown.
- **Rules first, models only for perception.** An optional on-device OCR stage (PaddleOCR models on ONNX Runtime, about 27 MB) turns screenshots and scanned PDFs into text with layout-aware line and column reconstruction; extraction itself stays rule-based and constraint-checked, and each event records whether its text came from OCR and with what confidence.
- **Entity resolution across venues.** The same work resubmitted or extended elsewhere is detected by title similarity during sync and linked as a resubmission chain instead of being merged or duplicated.
- **Local-first by design.** A single-user SQLite store with daily backups, credentials in the OS keyring, and an interface in Chinese or English.

## What is implemented

Everything in this guide is implemented and covered by the test suite (`pytest`); no part of the demo is mocked except the pre-seeded sync results in demo mode.
