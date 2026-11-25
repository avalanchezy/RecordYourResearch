# RecordYourResearch

> A privacy-first, local-only tracker for your own paper submissions.

[中文](README.md) · [Data sources](DATA_SOURCES.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

RecordYourResearch (Chinese name: 论文投稿追踪 / 投稿记录本) keeps every paper you have submitted in one place: where it went, its current status, how long it has been under review, when the revision is due, and where it was resubmitted after a rejection. It runs only on your own computer (it listens on `127.0.0.1` by default) and keeps all data in a local folder. There are no accounts, no telemetry and no CDN.

This is v0.4.0. The interface is in **English and Chinese**: switch with the **EN / 中文** button in the top bar. The choice is remembered, and a browser whose language is not Chinese starts in English. Labels below are quoted as they appear in the Chinese interface.

## Overview

- **OpenReview, fully automatic**: bind your own OpenReview account and sync all your submissions across venues (status, number of released reviews, decisions) with one click. While the app is running it syncs again about once a day.
- **Elsevier journals, semi-automatic**: look up the status on the official page yourself, then paste the page text into the app. It extracts the status, reviewer counts, dates and revision due date and records them on the timeline. There is also an experimental one-click query, off by default.
- **Other submission systems, semi-automatic (beta)**: IEEE, Springer, Taylor & Francis, Wiley, MDPI and Chinese journal systems such as 勤云, 方正 and 知网腾云. Log in to the official system yourself, copy the page that lists your manuscripts and paste it into 「从投稿系统导入」 (import from a submission system). The app parses each manuscript's number, title, status and date locally, and you review them before they go on the timeline. The app never logs in to these systems and makes no network calls for this. Anything it can't recognise can still be logged by hand.
- **Built first for Chinese CS/AI researchers**: venues show CCF ranks, CAS journal ranks (中科院分区表, upgraded edition 2025), XR journal ranks (新锐期刊分区表 2026) and CAS journal-warning badges, offline and out of the box. If you have a JCR licence, you can import JCR yourself.

The project is not affiliated with Elsevier, OpenReview, Clarivate, the National Science Library of the Chinese Academy of Sciences (中国科学院文献情报中心), 新锐学术 or the China Computer Federation (CCF). See the [disclaimer](#disclaimer).

### Current limitations

- Single user, single machine: no login, no sharing, no multi-device sync, no mobile app.
- Only OpenReview is fully automatic. For Elsevier and other submission systems you log in to the official page yourself (solving any captcha there) and paste the page. Anything you can't paste, or that isn't recognised, has to be logged by hand.
- 「从投稿系统导入」 is a beta. Parsing is rule-based and was tested against synthetic sample pages built from each system's public documentation, not real accounts. A redesigned system may not be recognised, or a status may land in the wrong stage, so check the preview before importing.
- PDF parsing is rule-based, and extracted data must be checked before you save it. Encrypted PDFs cannot be parsed; scanned PDFs (no text layer) need the optional OCR add-on.
- OpenReview accounts with multi-factor authentication (MFA) are not supported for automatic sync.
- OpenReview account sync covers venues on API v2 (api2, most venues since 2023). For older venues that exist only on the old API, fill in the forum id under 「编辑信息」 and use the single-paper query.
- JCR (impact factors and quartiles) is not bundled, because Clarivate forbids redistribution. If you have a licence, you can import it yourself.
- Windows 10/11 is the primary platform; macOS and Linux are best effort.

## Features

- **New-submission wizard ("＋ 新建投稿")**: drop a PDF and the app extracts the title, authors, abstract and keywords locally with PyMuPDF. It also picks up the conference or journal and year, including the headers of double-blind CVF, NeurIPS, ICLR, ICML, ECCV and AAAI submissions. From an Editorial Manager cover page it reads the manuscript number and corresponding author, and it finds DOI / arXiv ids and the cover-letter date. It preselects the tracking channel and autocompletes the venue name with ranking badges. Everything is a draft until you confirm it.
- **Status timeline**: one timeline per submission (newest first). Raw statuses from every channel are normalised to one set of stages:
  已提交 submitted → 编辑处理 editor → 外审中 under review → 审稿齐 reviews complete → 决策中 decision → 返修 revision → 已录用 accepted / 已拒稿 rejected / 已撤稿 withdrawn / 制作中 in production.
  The app shows days under review and days in the current stage. It warns you when a revision deadline is 3 days away or past, and nudges you when a paper has been with the editor for more than 4 weeks. Backdated entries never override the current status, and automatic channels only record real changes. Each entry shows its source: manual, OpenReview, Elsevier or 「粘贴·投稿系统」 (pasted from a submission system).
- **Overview ("总览")**: a "since your last visit" inbox lists every status change found by OpenReview sync or polling, including when you click 「同步我的投稿」 or 「立即全量轮询」 yourself. Manual entries, pasted official results, imports from a submission system, the single-paper 「立即查询」 and 「一键查稿」 are not listed. A bell badge and `(N)` in the tab title show the unread count. Desktop notifications are optional and only fire while the page is open, even in a background tab. The page checks for changes every 5 minutes and when you switch back to it. The overview also shows submissions found on OpenReview but not yet in your library (待建档: import, import and link, or ignore), reminders, stats and the stage distribution.
- **List ("投稿列表")**: categories 在投在审 / 已录用 / 已拒稿 / 已撤稿 / 归档 / 全部 / 回收站 (active, accepted, rejected, withdrawn, archived, all, recycle bin), with a stage filter, search and sorting. You can export the current list as CSV: it opens directly in Excel (UTF-8 with BOM), is safe against formula injection and has a dated filename. The top-bar 「导出台账 CSV」 exports everything (archived included, recycle bin excluded).
- **Detail drawer**: a latest-status card, the timeline, related submissions (转投 resubmitted / 扩写 extended / 会议版·期刊版 conference & journal versions…) and links to the OpenReview page or the submission system. Journal submissions on the manual channel also get a 「📋 从投稿系统导入」 button that jumps to the import panel. If you leave the submission-system link empty, the app infers it from the channel or manuscript number. The resubmit flow copies a rejected paper into a new submission and suggests CCF venues in the same field at the same or one lower rank. Deleting moves a submission to the recycle bin (回收站), where it can be restored; after 30 days it is permanently deleted together with its PDF (the purge runs with the daily background tasks, so not under `--no-daily`). Archiving, moving to the recycle bin, unlinking and deleting a timeline entry can be undone from the notification (「撤销」). Every view has its own URL (deep links, browser back/forward).
- **Ranking badges**: out of the box you get CCF-A/B/C (CCF list 2026), CAS rank 1–4 / Top (中科院分区表, upgraded edition 2025), XR rank 1–4 / Top (新锐期刊分区表 2026) and ⚠ warning badges (CAS warning lists 2024/2025 and the XR warning flag). Imported JCR data adds the JCR quartile and impact factor. Tooltips show the source and edition, e.g. 「中科院分区表升级版 2025（中科院文献情报中心）」 or 「新锐期刊分区表 2026（xr-scholar.com）」, and data you imported yourself is marked 「（你导入的数据）」. Badges use strict matching only, and workshops or "Findings" never inherit the main venue's rank.

## Screenshots

> All submissions, authors and numbers in the screenshots are made-up demo data.

![Overview: reminders, changes since your last visit and stats](docs/screenshot-overview.png)

![List: categories, stage filter and ranking badges](docs/screenshot-list.png)

![Detail drawer: latest status and timeline](docs/screenshot-drawer.png)

## Quick start

Requirements: Python 3.10 or newer (CI tests 3.10, 3.12 and 3.14). Windows 10/11 is the primary platform; macOS and Linux are best effort.

```bash
git clone https://github.com/avalanchezy/RecordYourResearch.git
cd RecordYourResearch
python -m pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:8765>. Press `Ctrl+C` in the terminal to stop.

A virtual environment is recommended:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt
python run.py
```

PowerShell may block `.venv\Scripts\activate` because of its execution policy. You can skip activation and call the environment's Python directly: `.venv\Scripts\python -m pip install -r requirements.txt`, then `.venv\Scripts\python run.py`.

**One-click start**: double-click `start.bat` on Windows, or run `./start.sh` (or `sh start.sh`) on macOS or Linux. The script creates `.venv`, installs the requirements, starts the app and opens your browser once the server is ready. It re-runs the install automatically if a previous install failed or the requirements changed. If something still looks broken, delete `.venv` and run the script again. Packages installed this way live only in `.venv`, so run the tools with `.venv\Scripts\python` (`.venv/bin/python` on macOS/Linux).

On first start the app creates the data folder (default `./data`) and builds the local ranking database from the bundled CCF list, warning lists, CAS 2025 and XR 2026 tables, which takes a few seconds.

**Try it with demo data**: one command starts a separate instance on port 8766 with made-up submissions in `demo-data/` (created on first run). It makes no network calls and leaves your real data alone; a **Demo data** badge marks it. Delete `demo-data/` to start over.

```bash
python run.py --demo --open
```

[docs/DEMO.md](docs/DEMO.md) is a demo script (a five-minute storyboard and booth scenarios) with sample pages to paste in [docs/demo/](docs/demo/). To fill a folder of your choice instead, run `python tools/demo_seed.py --data-dir PATH` and then `python run.py --data-dir PATH --no-daily --open`.

**Optional: read screenshots (OCR)**. Everything works without it. With it installed you can press `Ctrl+V` in a paste box with a **screenshot** of a submission-system page (or click "📷 识别截图" / "📷 Read screenshot" to pick an image). The screenshot is turned into text first and then goes through the usual parse preview → your confirmation → timeline. Scanned PDFs (no text layer) are read with OCR in the new-submission wizard too.

```bash
python -m pip install -r requirements-ocr.txt      # one-click start users: .venv\Scripts\python -m pip install -r requirements-ocr.txt
```

Restart the app afterwards. The engine is [RapidOCR](https://github.com/RapidAI/RapidOCR) (PaddleOCR models on ONNX Runtime): CPU only and offline, with Chinese and English models inside the package (about 27 MB). Screenshots never leave your computer.

- OCR only turns the image into text. Status, counts and dates are still extracted by the rule-based parsers and previewed before anything is saved. The recognised text stays in the box, so you can fix a misread character there.
- The preview shows how many lines were read and the lowest confidence; below 85 % it is highlighted so you check the numbers against the screenshot.
- Records written from a screenshot carry an "OCR" tag in the timeline, with the confidence on hover.
- Why not bundle a 1B-parameter vision model: it is large (1–2 GB), slow on a CPU, and a generative model can invent digits. For fields like reviewer counts and dates, rule-based parsing with consistency checks is more reliable.

**Tests**:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests use synthetic data only and block real network access.

> [!WARNING]
> The app binds to `127.0.0.1` by default and has **no login**. Anyone who can reach the port can read and change all your data. Never expose it with `--host 0.0.0.0` on a network you don't trust, and don't host it for other people. See [SECURITY.md](SECURITY.md).

## Configuration

```bash
python run.py --port 8765 --host 127.0.0.1 --data-dir PATH --no-daily --open
```

| Option | Default | Meaning |
|---|---|---|
| `--port` | `8765` | Port. If RecordYourResearch is already running on it, no second copy starts: with `--open` (start.bat and start.sh always pass it) the browser opens to the running one, otherwise the address is printed and run.py exits. If another program uses the port, pick a different one. |
| `--host` | `127.0.0.1` | Bind address. **Do not change it to `0.0.0.0`.** |
| `--data-dir PATH` | `./data` | Data folder |
| `--no-daily` | off | Disable the daily background tasks: automatic backup, recycle-bin purge and OpenReview daily sync |
| `--open` | off | Open the browser once the server is accepting connections |
| `--demo` | off | Demo mode: fictional data in `demo-data/` (created on first run), port 8766 unless `--port` is given, no daily tasks. Cannot be combined with `--data-dir` |

Environment variables: `PAPER_TRACKER_DATA_DIR=PATH` (same as `--data-dir`), `PAPER_TRACKER_PORT=8765` (default port; `--port` wins) and `PAPER_TRACKER_NO_DAILY=1` (same as `--no-daily`).

The data folder contains:

- `tracker.db`: the main database;
- `pdfs/`: attached PDFs;
- `uploads/`: PDFs being parsed. Unsaved ones older than 24 hours are removed at startup and by the daily task;
- `backups/auto/`: automatic and manual backups, of which the 14 most recent are kept;
- `rankings/rankings.db`: the local ranking database;
- `elsevier_device_id`: a random device id for the experimental Elsevier feature, created on the first one-click query or dictionary fetch.

## Data and privacy

- All your data stays in the data folder (default `./data`, excluded by `.gitignore`). Don't commit it or put it in a publicly shared folder.
- No telemetry, analytics, crash reports or update checks. Vue is bundled in `web/vendor/`, so nothing is loaded from a CDN. FastAPI's API debug pages (`/docs`, `/redoc`) and `/openapi.json` are turned off on purpose: the debug pages load scripts from a CDN, and the app doesn't need them.
- The app makes network calls in only two cases:
  1. If you bind an OpenReview account, it calls the official OpenReview API to log in and read your own submissions.
  2. Once you enable the experimental Elsevier one-click query, each query you click sends the manuscript number, the corresponding author's name in pinyin, the journal acronym and a random device id to Elsevier China's manuscript-query service (`dsp-api.elsevier.cn`). The 「拉取期刊缩写字典」 (journal-acronym dictionary) button, shown only while the feature is on, sends only the random device id.
- PDFs are parsed locally. The browser hands the file to the app running on your machine.
- 「从投稿系统导入」 adds no network calls: pasted text is parsed locally. The app never logs in to or contacts those submission systems and does not store their passwords.
- The browser's localStorage keeps only UI preferences, such as the list category and sort order.
- External links in the drawer (OpenReview page, submission system) open in your browser only when you click them.
- The database and PDFs are not encrypted on disk (only the OpenReview password is protected), so use full-disk encryption.

## Channels

### OpenReview (automatic)

The OpenReview API no longer allows anonymous reads (they get 403 ChallengeRequired), so you have to bind your own account on the "渠道查询" (channels) page.

- **Credential storage**: on Windows, DPAPI encryption bound to your Windows user. On macOS and Linux, the Keychain or Secret Service via the `keyring` package. If no secure store is available, the app **refuses to store** the password. The login token is kept in memory only. Unbinding deletes the stored credential, including the copies in backup files.
- **Sync my submissions ("同步我的投稿")**: pulls every OpenReview submission you are an author of, across all venues, and matches them to your library by forum id or by same venue and title. Status comes from the venue id, released reviews and meta-reviews, decisions, withdrawals and desk rejections; TMLR states are supported too. Unknown submissions appear as 待建档 items for you to import or ignore. Account sync covers venues on OpenReview's API v2 (api2, most venues since 2023). For older venues that exist only on the old API, fill in the forum id under 「编辑信息」 and use the single-paper query.
- **Daily background sync**: while the app runs, it syncs your account about once every 23 hours. It then polls OpenReview papers that were not checked in the last 6 hours, 1 second apart, and catches up after sleep or restart.
- **Staying gentle**, in background and manual syncs and polls alike:
  - It stops at the first login failure.
  - On ChallengeRequired it never tries to get around the challenge and does not fall back to the old API; it stops the round.
  - On HTTP 429 it waits for Retry-After (at most 60 s), retries once and stops the round if it is still rate-limited.
- **Single-paper query and full poll**: 「OpenReview 立即查询」 in the drawer and 「立即全量轮询」 on the 渠道查询 page. Without a forum id, the app matches by title within the same venue; the titles must be identical once case, spaces and punctuation are ignored. You can also enter the forum id under 「编辑信息」.
- **Venue-id templates**: for NeurIPS, ICLR, ICML, CVPR, ICCV, ECCV, COLM, AAAI, IPMI, ACCV, BMVC and TMLR, the venue id is assembled from the venue name and year (e.g. `NeurIPS.cc/2026/Conference`). For other venues, enter the venue id yourself.
- **MFA accounts are not supported yet.** Track those papers manually; please don't turn off MFA for this tool.

> [!IMPORTANT]
> Run the app locally, for the account owner only. Don't host it for other people or hold their OpenReview accounts: OpenReview's terms do not allow letting third parties use the system on your behalf.

### Elsevier journals (semi-automatic)

**Recommended: paste the official result.** Open an official Elsevier status page yourself, such as the Editorial Manager author centre, Track your submission or Elsevier China's manuscript query page (Chinese and English pages are both supported), and solve any captcha. Copy the whole page, then in the drawer click "📋 粘贴官方结果" → "🔍 解析预览" (preview) → "✓ 记入时间线" (record). The adaptive parser extracts the status, reviewer counts (invited / accepted / completed), dates, the revision due date, the manuscript number and the journal name. It refuses to write if the pasted manuscript number does not match the paper (ignoring revision suffixes such as R1), and it skips results identical to the previous one. When a reviewer count has changed, the official page shows "old → new" with an arrow icon, so the copied text glues the two numbers together (for example "02" for 0 → 2, "23" for 2 → 3); the parser splits them using the counts recorded last time and the rule completed ≤ accepted ≤ invited, shows the change in the preview, and highlights the rare case where two readings remain plausible. If copying is awkward (say you looked it up in WeChat on your phone), paste a screenshot instead; this needs the optional OCR add-on. Counts the official page hides are shown as "5+". With a revision status it also saves the revision due date, and the official submission date replaces the one you entered. If nothing is recognised (for example, you pasted a captcha page), nothing is written.

**Experimental: Elsevier one-click query ("爱思唯尔一键查稿"), off by default.** Turn it on explicitly with "开启实验功能…" in the "爱思唯尔期刊" panel of the "渠道查询" page. Elsevier papers then get a "一键查稿（实验）" button ("查" in the list). It queries Elsevier China's manuscript-query service with the manuscript number, the corresponding author's pinyin (e.g. `XIAOMING WANG`) and the journal acronym (usually the prefix of a number like `ABC-D-25-00000`). It runs only when you click, at most once per manuscript per 24 hours. On any captcha or risk-control response it stops and asks you to look the status up manually, then paste it. If the service returns a status the app does not recognise, nothing is written; log it manually instead. While the feature is on, the 「拉取期刊缩写字典」 button in the same panel fetches the journal-acronym list from the same service, sending only the random device id.

> [!CAUTION]
> This is not an official API that Elsevier offers to third-party tools. It may stop working at any time and may conflict with Elsevier's website terms. Use it at your own risk.

### Import from a submission system (semi-automatic, beta)

**Why paste instead of logging in.** Only OpenReview offers an author API that permits automated access, which is why OpenReview is fully automatic. IEEE, Taylor & Francis, Springer, Wiley, Elsevier, MDPI and the others have no author API, and their terms forbid scripted login or crawling. For example, the Elsevier / Editorial Manager terms forbid robots and systematic retrieval, and the EasyChair and Microsoft CMT terms forbid automated access. Their logins often have captchas, which the app never bypasses. So the app **never logs in to or contacts** these systems and does not store their passwords.

**How it works**:

1. Log in to the official submission system yourself.
2. Open a page that lists your manuscripts, or one manuscript's status page. Menu pages without a manuscript list yield nothing:
   - Editorial Manager: from the Author Main Menu, open a folder such as Submissions Being Processed or Submissions with a Decision, and copy that page;
   - ScholarOne: the Author Dashboard queue pages;
   - IEEE Author Portal and Wiley Research Exchange: the My Submissions page.
3. Press `Ctrl+A`, `Ctrl+C` and paste into the 「从投稿系统导入」 panel on the 渠道查询 page. Pasting parses automatically; there is also a 「🔍 解析预览」 (preview) button.

Journal submissions on the manual channel have a 「📋 从投稿系统导入」 button in the drawer that jumps to the panel.

**Recognised systems** (detected from the page text):

- ScholarOne Manuscripts (IEEE legacy UI, Taylor & Francis, Wiley, SAGE, OUP, IOP, ACM journals, 自动化学报);
- Editorial Manager (Springer journals not yet on Snapp, Elsevier, PLOS…);
- IEEE Author Portal and Wiley Research Exchange;
- Springer Nature Snapp and Nature journals' tracking, plus Research Square timelines;
- MDPI SuSy, the Taylor & Francis Submission Portal and Frontiers;
- Chinese journal systems such as 勤云, 方正 and 知网腾云 (e.g. 软件学报, 中国图象图形学报, 计算机学报).

Unknown systems get a best-effort generic parse.

**The preview table** shows, for each manuscript, the ID, title, official status → stage and date, plus what will happen:

| Preview | Meaning |
|---|---|
| 记入时间线 (record) | Matches a submission in your library; the new status is recorded on import |
| 已是最新 (up to date) | Nothing new; nothing is written |
| 新建 (new) | No match; a new submission will be created. The journal name is required, and you can fill it in or correct it in the preview |
| 状态未识别 (unrecognised) | Shown, but cannot be imported |
| 草稿 / 已被取代，不导入 (draft / superseded) | Drafts that were never submitted, records replaced by a newer one, failed submissions, and items in Editorial Manager's Incomplete or Waiting for Author's Approval folders. Shown, but cannot be imported or created |
| 缺标题，不能新建 (no title) | No match, and the page shows no title to create one from |
| 对上了多篇，请手动处理 (several matches) | Several candidates, or two different pasted manuscripts claim the same record. The candidate #ids are listed; cannot be imported |
| 在回收站里（先恢复再导入） (in the recycle bin) | The matched submission is in the recycle bin; restore it first |

**How rows are matched to your library**:

- Rows with a manuscript number match by number only: an identical number wins; otherwise the number is compared ignoring revision suffixes such as R1 or .R1.
- Rows without a number match by identical title only, and only against journal submissions. The journal name must agree when the page shows one, and a rejected or withdrawn record never takes a new in-progress status.
- Truncated titles (ending in "…") may match by prefix, with at least 6 CJK characters or 20 Latin characters.
- Title matches show the matched record's title and venue with the hint 「按标题对上，请核对」 (matched by title, please check) and are **not** pre-ticked. Number matches and 新建 rows are pre-ticked.

After checking, tick the rows you want and click 「✓ 导入选中」 (import selected).

**On import**:

- Each status is recorded on the timeline with the source 「粘贴·投稿系统」, at the official date when the page shows one.
- If the official date is earlier than the submission's registration entry (for example, a record created today without a submission date), the registration entry moves before it, so the imported status becomes current, as with the Elsevier paste. If the timeline already has a later entry, the imported status goes into the history and the notification says the current status did not change.
- The manuscript number and submission date are filled in if they are empty.
- Pasting the same page again records nothing twice: an entry with the same stage, text and day is skipped. If one paste lists the same manuscript twice, it is created only once.
- New submissions are created on the manual channel, with a note saying which system they came from.
- Imports are not listed in 「自上次查看以来」, just like pasted Elsevier results.

**No network**: everything is parsed locally from the text you paste; nothing is sent anywhere.

**Elsevier**: the per-paper 「粘贴官方结果」 (which also reads reviewer counts) stays. The new import also works with Elsevier's Editorial Manager: from the Author Main Menu, open a folder such as Submissions Being Processed and copy that page (the menu page itself lists no manuscripts).

> [!NOTE]
> This is a beta feature. Parsing is rule-based and was tested against synthetic sample pages built from each system's public documentation, not real accounts, so some statuses will not be recognised or will land in the wrong stage. Check the preview before importing. If that happens, log the status manually and open an "Unrecognized status" issue with only the raw status text, the system and the expected stage. **Never** paste whole pages, manuscript numbers or titles into issues.

### Manual logging (fallback)

When the import doesn't recognise your system or status, or you can't copy the page, use "＋ 登记状态变化" (log a status change) in the drawer. Pick a stage and, optionally, a raw status from an Editorial Manager / ScholarOne-style list (Submitted to Journal, With Editor, Under Review, Required Reviews Complete, Decision in Process, Revise - Major, …). You can backdate an entry and set a revision due date.

## Ranking data

| Dataset | Included | Notes |
|---|---|---|
| CCF list of recommended international conferences and journals (7th edition, 2026) | yes | `data-sources/ccf_2026.json` |
| CAS international journal warning lists 2024 & 2025 | yes | `data-sources/cas_warning_lists.json` |
| 中科院分区表 (CAS journal ranking, upgraded edition) 2025 | yes | `data-sources/cas_2025.csv`, 21,772 journals |
| 新锐期刊分区表 (XR journal ranking) 2026, xr-scholar.com | yes | `data-sources/xr_2026.csv`, 22,299 journals |
| JCR | no | Clarivate forbids redistribution; import your own if you have a licence |

- The four bundled files work offline, out of the box. The data comes from its original publishers, who keep all rights, and it is **not** covered by the AGPL.
- `ccf_2026.json` and `cas_warning_lists.json` are this project's own transcriptions of the official PDF and public notices.
- The CAS and XR tables (`cas_2025.csv`, `xr_2026.csv`) are unmodified, byte-identical copies of the corresponding files in the public [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) mirror; `data-sources/SHA256SUMS` lets you verify them. We bundle them so researchers get badges offline, out of the box.
- Rights holders who object can open an issue with the ["Data source concern" template](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md), and we will remove the file promptly.
- According to a [report by IT之家 (ITHome)](https://www.ithome.com/0/934/100.htm), the National Science Library of CAS announced on 2026-03-27 that it stops updating the journal ranking from 2026, so the 2025 edition is the last one.
- Sources, editions, entry counts and checksums are in [DATA_SOURCES.md](DATA_SOURCES.md).

**Importing or replacing data**:

```bash
python tools/build_rankings.py                  # rebuild from the bundled data only
python tools/build_rankings.py --jcr FILE       # add JCR data you are licensed to use
python tools/build_rankings.py --xr FILE        # replace the bundled XR table, e.g. with a newer edition
```

- If you installed with start.bat or start.sh, use `.venv\Scripts\python` (`.venv/bin/python` on macOS/Linux) instead of `python`.
- `--jcr FILE` adds JCR. `--cas FILE` and `--xr FILE` replace the bundled table with your CSV. You can combine them.
- Each run rebuilds the whole ranking database, so pass every CSV you want to keep.
- Optional `--cas-edition`, `--xr-edition` and `--jcr-edition` labels appear in badge tooltips; otherwise the edition comes from the CSV's 年份 column (CAS, XR) or the `IF(<year>)` header (JCR). Use `--data-dir PATH` if you changed the data folder.
- Restart the app afterwards. Data you imported is marked 「（你导入的数据）」 in tooltips.
- When a later version ships updated bundled data, startup rebuilds the ranking database automatically. If you imported your own CSVs, the app keeps your database and logs a message, and the 分级速查 card on the 渠道查询 page tells you to rerun `tools/build_rankings.py` with your CSVs.
- Keep the CSVs you import (JCR in particular), and any `rankings.db` that contains them, inside your data folder. Never commit or share them.

Columns for a JCR CSV or a replacement CAS/XR CSV (header row; saved as UTF-8 or GBK, so Excel's 「CSV（逗号分隔）」 format on Chinese Windows works; required columns are listed in [DATA_SOURCES.md](DATA_SOURCES.md#english-summary)):

- CAS: `Journal`, `ISSN/EISSN`, `大类`, `大类分区`, `Top`, `Web of Science`
- XR: `Journal`, `ISSN`, `EISSN`, `大类中文名`, `大类新锐分区`, `Top`, `预警标记`
- JCR: `Journal`, `ISSN`, `EISSN`, `IF(<year>)`, `Category_1`, `IF Quartile(<year>)_1`

## Backup and restore

- The app backs up the database every day to `data/backups/auto/`; the first check runs about 90 seconds after start. You can also click "立即备份" (back up now) on the "渠道查询" page. Manual backups go into the same folder, and the 14 most recent copies are kept in total. The backups card shows the folder relative to the data folder, and 「复制路径」 copies the full path. Before a schema migration the app also keeps a `premigrate-*.db` copy.
- Backups contain the database only, **not the PDFs**. For a full backup, stop the app and copy the whole data folder.
- `--no-daily` also disables automatic backups.
- **Restore**: stop the app, keep a copy of the current `data/tracker.db`, copy the backup over `data/tracker.db`, then start the app again. After restoring on another computer or user account, unbind and re-bind OpenReview, because the saved password can only be read where it was stored.

## FAQ

- **Why must I bind an OpenReview account?** Anonymous API reads are blocked. The app only reads what your account can see.
- **"Needs re-binding" after moving to a new computer?** Expected: stored passwords are tied to the original machine and user. On the 「渠道查询」 page, unbind and bind your OpenReview account again.
- **The Elsevier query says a captcha is required.** By design the app never bypasses it. Look the status up on the official page and paste it.
- **Can IEEE or Springer sync automatically like OpenReview?** No. These systems have no author API, their terms forbid scripted login or crawling, and their logins often have captchas, so the app never logs in for you. Use 「从投稿系统导入」: you log in and copy the page, and the app parses it locally.
- **How do I import from a submission system?** Log in and open a page that lists your manuscripts: an Editorial Manager folder such as Submissions Being Processed, a ScholarOne Author Dashboard queue, or My Submissions on IEEE / Wiley. One manuscript's status page also works. Press `Ctrl+A`, `Ctrl+C` and paste into 「从投稿系统导入」 on the 渠道查询 page. Check the preview (title matches are not pre-ticked, so confirm each one), tick the rows and click 「✓ 导入选中」. It is a beta, so review before importing.
- **A status was not recognised or went to the wrong stage.** Log it manually, then open an "Unrecognized status" issue with only the raw status text, the channel or submission system and the expected stage. Don't paste whole pages, manuscript numbers, titles or screenshots.
- **No badge or no CAS rank for a journal?** The CAS and XR tables are bundled, but badges use strict matching only: try the journal's full name under 「编辑信息」, or look it up in 分级速查. The ranking tables cover journals only; conferences get CCF ranks. JCR is not bundled and must be imported.
- **Does it keep syncing when the browser is closed?** Yes, as long as `run.py` is running. Desktop notifications need the page open.
- **Can I host it for my lab?** No. There is no login, and OpenReview's terms forbid third-party use on your behalf. Everyone should run their own copy.
- **Port 8765 is in use.** If RecordYourResearch is already running there, starting it again does not start a second copy; with `--open` (or start.bat / start.sh) it opens the browser to the running one. If another program uses the port, use `--port 8766`.
- **start.bat says Python is not found.** Install Python 3.10 or newer (CI tests 3.10, 3.12 and 3.14) and tick "Add python.exe to PATH" during installation.
- **PowerShell refuses to run `activate`.** That is PowerShell's execution policy. Skip activation and call `.venv\Scripts\python run.py` directly.
- **A tool says a module is missing.** The packages live in `.venv`; run tools with `.venv\Scripts\python` (`.venv/bin/python` on macOS/Linux). If start.bat or start.sh itself looks broken, delete `.venv` and run the script again.

## Disclaimer

- This is an independent open-source project. It is not affiliated with, endorsed or sponsored by Elsevier, OpenReview, Clarivate, the National Science Library of CAS, 新锐学术, CCF, or the other publishers and submission systems mentioned here (IEEE, Springer Nature, Taylor & Francis, Wiley, MDPI and others). Names and trademarks belong to their owners and are used only to describe compatibility. No logos are used.
- Status information comes from your own entries, pasted official pages or responses from official services, and may be late or wrong. **The official submission system and the editorial office are always authoritative.** Don't rely on this tool for deadlines.
- Ranking badges are for reference only and are not an evaluation of any venue or paper; **the publishers' official data always prevails**. Bundled data may be outdated or contain transcription errors. CCF itself says its list is not meant as a basis for academic evaluation.
- The Elsevier one-click query is unofficial and experimental. Use it at your own risk.
- You are responsible for complying with the terms of the services you use, including each submission system.
- The software is provided "as is", without warranty; see sections 15 and 16 of the AGPL-3.0.

## Licence

- Code: [GNU AGPL-3.0-or-later](LICENSE), because the project depends on PyMuPDF, which is licensed under AGPL-3.0 (or an Artifex commercial licence).
- Files under `data-sources/` are **not** covered by the AGPL; their publishers keep all rights. See [DATA_SOURCES.md](DATA_SOURCES.md).
- The bundled Vue 3.4.38 (`web/vendor/vue.global.prod.js`) is MIT-licensed; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for all third-party licences.
- Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues privately as described in [SECURITY.md](SECURITY.md).
