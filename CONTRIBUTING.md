# 参与贡献 / Contributing

感谢你愿意改进 RecordYourResearch！这是一个给个人研究者用、只在本机运行的小工具。动手之前请先读一下下面的原则。英文摘要见[文末](#english-summary)。

## 项目原则

- **本机优先、单人使用**：程序只服务本机（`127.0.0.1`），没有账号体系；不做托管服务，也不做多人共享。
- **隐私优先**：不加遥测、统计、崩溃上报或更新检查；网页不从 CDN 加载资源。任何新的对外网络请求都要先开 issue 讨论，而且必须由用户主动开启、可以关闭。
- **尊重第三方服务**：只用官方提供的方式访问 OpenReview 等服务，请求保持低频；**不绕过验证码、人机验证、限流或任何访问控制**，遇到就停下来转人工。没有作者 API 的投稿系统（IEEE、Springer、Elsevier、MDPI 等）只支持用户自己复制粘贴页面：不接受替用户登录、抓取这些系统或保存它们密码的实现。
- **依赖尽量少**：新增 Python 依赖请先开 issue 讨论；前端不引入构建工具，Vue 以随项目附带的文件提供。
- **界面用中文**，用语和现有的阶段名、按钮文字保持一致。
- **只用合成数据**，见[下文](#只用合成数据)。

## 开发环境

需要 Python 3.10 或更高（CI 测试 3.10 / 3.12 / 3.14）。

```bash
git clone https://github.com/avalanchezy/RecordYourResearch.git
cd RecordYourResearch
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python tools/demo_seed.py --data-dir ../ryr-dev-data      # 可选：灌入一批虚构的演示投稿
python run.py --data-dir ../ryr-dev-data --no-daily
```

- Windows 的 PowerShell 可能因为执行策略（Execution Policy）拦截 `.venv\Scripts\activate`。不激活也可以：把上面命令里的 `python` 换成 `.venv\Scripts\python`（macOS / Linux 为 `.venv/bin/python`）。
- `requirements-dev.txt` 已包含 `requirements.txt`。
- 开发时请用单独的数据目录（放在仓库外面，例如 `../ryr-dev-data`），并加 `--no-daily` 关掉后台任务，免得碰到你自己的真实数据。在开发或演示数据目录里绑定 OpenReview 账号也不会覆盖真实数据目录的绑定：macOS / Linux 上钥匙串条目按数据目录区分（账号名为 `openreview:<数据目录路径散列的前 12 位十六进制>`），Windows 上密文本来就存在各自的 `tracker.db` 里。
- 前端改 `web/index.html`、`web/app.js`、`web/style.css` 后刷新浏览器即可，没有构建步骤；改了 Python 代码需要重启 `run.py`。
- FastAPI 自带的 `/docs`、`/redoc` 和 `/openapi.json` 是特意关闭的（调试页会从 CDN 加载脚本），要看有哪些接口请直接读 `server.py`。

## 运行测试

```bash
python -m pytest
```

- `tests/conftest.py` 会为测试创建临时数据目录、关闭后台任务，并屏蔽真实网络：httpx 的传输层会直接抛错。应用代码会把它当成普通的网络故障来处理（例如返回 `ok: false`），所以一个意外的真实请求本身**不会**让测试失败。测试必须对结果做断言。
- 需要 OpenReview 返回值时用 `fake_openreview` fixture（它替换了 `app.openreview` 里的 `get_token`、`get_my_profile`、`fetch_my_submissions`），需要 HTTP 接口时用 `client` fixture（FastAPI TestClient）；在 macOS / Linux 上需要安全存储时用 `secure_store` fixture（内存钥匙串）。
- 需要 PDF 时，像 `tests/test_parsing.py` 那样在测试里用 PyMuPDF 现场生成一份内容虚构的 PDF，不要提交真实论文。
- 不要在测试里使用真实的账号或密码。
- CI 会在 Windows、macOS、Linux 上用 Python 3.10、3.12 和 3.14 运行测试。

## 代码结构

| 路径 | 内容 |
|---|---|
| `run.py` | 启动入口和命令行参数 |
| `server.py` | FastAPI 应用：全部 API、本机访问限制（Host / Origin 检查与安全响应头）、爱思唯尔官方页面的粘贴解析、CSV 导出、每日后台任务 |
| `app/config.py` | 版本号、数据目录等运行配置 |
| `app/db.py` | SQLite 表结构、数据迁移、状态事件流水、备份 |
| `app/stages.py` | 阶段定义、原始状态映射表 `RAW_STATUS_MAP` 和兜底正则规则 |
| `app/openreview.py` | OpenReview 官方 API：登录、同步我的投稿、状态推断 |
| `app/elsevier.py` | 爱思唯尔一键查稿（实验功能，默认关闭） |
| `app/parser.py` | PDF 元数据抽取（PyMuPDF） |
| `app/syspaste.py` | 「从投稿系统导入」：识别粘贴的投稿系统页面，解析出稿件编号、标题、状态和日期（不联网） |
| `app/rankings.py` | 分级匹配：徽章用严格匹配，速查和联想用模糊匹配 |
| `app/rankings_build.py` | 生成本地分级库 `rankings.db` |
| `app/vault.py` | 凭据存储（Windows DPAPI / keyring） |
| `app/venueutil.py` | 会议 / 期刊名与年份处理、标题相似度 |
| `web/index.html` | 界面模板（Vue 的 in-DOM 模板） |
| `web/app.js` | 前端逻辑 |
| `web/style.css` | 样式 |
| `web/vendor/vue.global.prod.js` | 随项目附带的 Vue 3.4.38，**不要修改** |
| `tools/build_rankings.py` | 重建分级库、导入或替换分级数据的命令行工具 |
| `tools/parse_ccf.py` | 从 CCF 官方 PDF 生成 `data-sources/ccf_2026.json` |
| `tools/demo_seed.py` | 往一个空数据目录里灌入虚构的演示投稿（试用、截图、调试用） |
| `data-sources/` | 随项目附带的分级数据和 `SHA256SUMS`，权利归各自的发布方（见 [DATA_SOURCES.md](DATA_SOURCES.md)） |
| `tests/` | pytest 测试 |

关于 `web/index.html`：它是 in-DOM 模板，浏览器先把它当普通 HTML 解析，Vue 再在运行时编译。所以属性名会被浏览器转成小写（绑定时请用 kebab-case）；`<table>`、`<select>` 等元素里只能放 HTML 允许的子元素。

## 添加一个状态映射

最常见的贡献，是让程序认识一种新的状态文字（通常来自「未识别的状态」issue）。OpenReview 和爱思唯尔粘贴用的是 `app/stages.py` 里的映射，下面 1–5 步说的就是它；「从投稿系统导入」的状态另见第 6 步。

1. **算出归一键**。`app/stages.py` 里的 `status_key()` 会把原始文字转成小写、把各种破折号统一成 ` - `、合并空白、去掉首尾标点，例如 `Revise – Minor.` → `revise - minor`。先确认它现在的结果：

   ```bash
   python -c "from app.stages import status_key, normalize; s = 'Awaiting Author Approval'; print(status_key(s), normalize(s))"
   ```

2. **加进 `RAW_STATUS_MAP`**。在对应阶段的分组里加一行，键是归一键，值是阶段（`submitted`、`editor`、`review`、`reviews_done`、`decision`、`revise`、`accepted`、`rejected`、`withdrawn`、`production` 之一）：

   ```python
   "awaiting author approval": "submitted",   # 示例
   ```

   精确键优先于兜底正则 `_RULES`。只有一类写法确实变体很多时才考虑改正则：正则按顺序取第一个命中，否定和更具体的短语必须排在前面（例如 `Not accepted` 不能落到已录用，`Accept with Minor Revision` 应归为返修）。

3. **加测试**。在 `tests/test_parsing.py` 的 `test_status_normalisation` 里加一行断言，或者新写一个测试：

   ```python
   assert stages.normalize("Awaiting Author Approval") == "submitted"
   ```

   改了正则的话，再加几个「不应被误判」的反例。

4. **中文状态**。爱思唯尔官方页面的粘贴解析要在 `server.py` 里改两处：`_ZH_STAGE` 字典和 `_PASTE_PATTERNS["stage_zh"]` 正则。同时把它加进 `app/stages.py` 的 `RAW_STATUS_MAP`，并补一个粘贴解析的测试（参考 `tests/test_api.py` 的 `test_official_paste_parser_records_status`）。

5. （可选）很常见的状态可以加进手动登记的下拉选项：`web/app.js` 里的 `rawStatusOptions`。

6. **「从投稿系统导入」的状态**。导入时先用 `app/syspaste.py` 里各投稿系统专用的词表 `EXTRA_STATUS`（优先级最高）和它自己的规则，认不出时才退回 `app/stages.py` 的映射。所以要修正导入的状态，请改 `EXTRA_STATUS`，并补一个合成的测试：写在 `tests/test_system_import.py` 里，或者加进 `tests/fixtures/syspaste_corpus.json` 语料（由 `tests/test_syspaste_corpus.py` 检查）。测试页面同样必须是合成的。

## 只用合成数据

- 代码、测试、fixture、示例、截图、issue、PR 和提交信息里，**一律不要**出现真实的论文标题、稿件编号、人名、邮箱、OpenReview forum ID、PDF 或数据库。
- 用一眼就能看出是虚构的例子：稿件编号 `ABC-D-25-00000`、标题「DemoNet: A Synthetic Network for Testing」、作者 Alice Example、邮箱 `alice@example.com`、拼音 `XIAOMING WANG`。
- 截图请用 `tools/demo_seed.py` 生成的演示数据，或在一个全新的数据目录（`--data-dir`）里手动建几篇演示投稿后再截。
- 「从投稿系统导入」的测试页面必须是合成的：照各系统的公开文档和界面结构自己构造，填虚构的稿件。**不要**提交从真实作者中心复制来的页面，哪怕已经打码。
- 不要提交数据目录、`*.db`、PDF、你自己导入用的 CSV（例如 JCR）或 `rankings.db`。`.gitignore` 已经排除了这些文件，请不要用 `git add -f` 绕过。`data-sources/` 里的随附数据文件按下一节的规则维护。

## `data-sources/` 的贡献

- **CSV 按字节原样保存**：`.gitattributes` 里对 `data-sources/*.csv` 和 `data-sources/SHA256SUMS` 设置了 `-text`，Git 不做换行转换；两个 JSON 文件按普通文本处理。请不要用会改动编码、换行符或文件末尾的工具「整理」这些 CSV。
- `cas_2025.csv`、`xr_2026.csv` 是上游公开镜像中文件的原样副本，不在本仓库里修改内容；要更新，就把它们换成上游的新文件，同样原样复制。
- `ccf_2026.json`、`cas_warning_lists.json` 是本项目根据公开资料整理的转录。修正转录错误时，请在 PR 里附上官方来源（链接，以及对应的领域、类别、序号或通知）；改 JSON 不需要动 `SHA256SUMS`。
- **同步更新 `SHA256SUMS`**：它只记录上游原样副本（CSV）。新增或替换 CSV 之后，在 `data-sources/` 目录里运行 `sha256sum cas_2025.csv xr_2026.csv > SHA256SUMS`（Windows 上请在 Git Bash 里运行；不要用 PowerShell 的 `>`，它会写成 UTF-16），再用 `sha256sum -c SHA256SUMS` 确认（macOS 用 `shasum -a 256 -c SHA256SUMS`）。测试会检查 `SHA256SUMS` 正好列出 `data-sources/` 里的全部 CSV 文件；`<hash>  <name>` 和 `<hash> *<name>` 两种写法都可以，Git Bash 默认的输出就行。
- **写清来源**：在 `DATA_SOURCES.md` 里记录发布方、版本或发布日期、获取途径（上游仓库、文件路径和 commit）和条目数，上游副本还要写 SHA-256，中英文两部分都要更新。
- 新增随附数据集请先开 issue 讨论。不接受 JCR 数据（Clarivate 禁止再分发），也不接受抓取任何分级数据的脚本。
- 只报告问题、不提交修改时，请用「数据来源异议 / Data source concern」issue 模板（转录错误和权利异议都用它）。

## 提交 PR

- 一个 PR 只做一件事，尽量小。
- 说明改动的原因和用户能看到的变化；界面改动请附上用演示数据截的图。
- 行为有变化时，同步更新 `README.md` 和 `README.en.md`（以及需要的话 `DATA_SOURCES.md`、`SECURITY.md`）。
- 确认 `pytest` 全部通过。

## 许可

本项目以 AGPL-3.0-or-later 发布。提交贡献即表示你同意以 AGPL-3.0-or-later 授权你的贡献，并确认你有权这样授权（例如代码是你自己写的，或来自许可证兼容的来源并已注明出处）。

例外：`data-sources/` 里的第三方数据不在 AGPL 授权范围内，权利归其发布方所有。提交这类文件只表示你确认：它是所注明上游文件未经修改的原样副本，或是依据所注明的公开来源整理的。

---

## English summary

Thanks for helping! RecordYourResearch is a small local-only tool for individual researchers. The UI is in Chinese; issues and PRs in English are welcome.

- **Principles**:
  - local-first and single-user, with no hosting or sharing;
  - no telemetry and no CDN; new outbound requests must be discussed first and be opt-in;
  - respect third-party services: official APIs only, low request rates, and never bypass captchas, challenges, rate limits or access controls. Submission systems without an author API (IEEE, Springer, Elsevier, MDPI, …) are supported only through pages the user copies and pastes; no code that logs in to them, crawls them or stores their passwords;
  - keep dependencies minimal;
  - keep UI text in Chinese.
- **Setup**: Python 3.10 or newer (CI tests 3.10, 3.12 and 3.14).
  - Run `python -m pip install -r requirements-dev.txt`, then `python run.py --data-dir ../ryr-dev-data --no-daily`. If PowerShell blocks `.venv\Scripts\activate` (execution policy), skip activation and use `.venv\Scripts\python` instead of `python`.
  - A separate data folder keeps your real data out of the way, and `python tools/demo_seed.py --data-dir ../ryr-dev-data` fills it with made-up submissions; use that demo data for screenshots.
  - Binding OpenReview in a dev or demo folder does not overwrite your real binding: on macOS/Linux the keychain entry is named `openreview:<12-hex hash of the data folder path>`, and on Windows the ciphertext lives in each folder's `tracker.db`.
  - FastAPI's `/docs`, `/redoc` and `/openapi.json` are turned off on purpose; read `server.py` for the API.
- **Tests**: run `python -m pytest`.
  - Network access is blocked because httpx's transport raises. The app treats that as a network failure, so a real request does not fail a test by itself; tests must assert on results.
  - Use the `client`, `fake_openreview` and `secure_store` fixtures from `tests/conftest.py`, and generate PDFs on the fly with PyMuPDF.
- **Code layout**: `server.py` is the FastAPI app, `app/*.py` holds the modules (database, stages, OpenReview, Elsevier, PDF parser, submission-system paste parser `syspaste.py`, rankings, vault) and `web/` the in-DOM Vue template, `app.js` and `style.css`. Never edit `web/vendor/`.
- **Adding a status mapping** for OpenReview and the Elsevier paste (`app/stages.py`; for the submission-system import see step 5):
  1. Add the `status_key()` form of the raw status to `RAW_STATUS_MAP` in `app/stages.py`.
  2. Add an assertion to `test_status_normalisation` in `tests/test_parsing.py`.
  3. If you change the regex fallback rules (`_RULES`), add negative tests; order matters.
  4. For Chinese statuses, change two places in `server.py`: the `_ZH_STAGE` dict and the `_PASTE_PATTERNS["stage_zh"]` regex. Also add the status to `RAW_STATUS_MAP` in `app/stages.py`, and add a paste-parser test (see `test_official_paste_parser_records_status` in `tests/test_api.py`).
  5. Submission-system import: statuses first go through `app/syspaste.py`'s system-specific vocabulary `EXTRA_STATUS` (which takes precedence) and its own rules, and only then fall back to `app/stages.py`. To fix an import status, edit `EXTRA_STATUS` and add a synthetic test in `tests/test_system_import.py` or the corpus fixture `tests/fixtures/syspaste_corpus.json` (checked by `tests/test_syspaste_corpus.py`).
- **Synthetic data only**: never put real titles, manuscript numbers, names, emails, OpenReview forum ids, PDFs or databases in code, tests, screenshots, issues, PRs or commit messages. Use `ABC-D-25-00000`, "DemoNet: …", Alice Example, `alice@example.com`. Test pages for the submission-system import must be synthetic, built from each system's public documentation; never commit a page copied from a real author dashboard, even redacted.
- **`data-sources/`**:
  - The CSV files and `SHA256SUMS` are stored byte-exact (`data-sources/*.csv -text` and `data-sources/SHA256SUMS -text` in `.gitattributes`); don't let tools reformat them. The two JSON files get normal text handling.
  - `cas_2025.csv` and `xr_2026.csv` are unmodified copies of upstream mirror files. Update them by replacing them with newer upstream files, never by editing.
  - `ccf_2026.json` and `cas_warning_lists.json` are our own transcriptions. Fixes need an official source; they don't touch `SHA256SUMS`.
  - `SHA256SUMS` covers only the upstream CSV copies. After adding or replacing a CSV, run `sha256sum cas_2025.csv xr_2026.csv > SHA256SUMS` in `data-sources/`. On Windows, use Git Bash, not PowerShell's `>`, which writes UTF-16. Verify with `sha256sum -c SHA256SUMS`, or `shasum -a 256 -c SHA256SUMS` on macOS. A test checks that `SHA256SUMS` lists exactly the CSV files in `data-sources/`; both `<hash>  <name>` and `<hash> *<name>` are accepted, so Git Bash's default output is fine.
  - Document the provenance in `DATA_SOURCES.md`, in both languages: publisher, edition or release date, where it came from (upstream repository, path, commit) and entry count, plus the SHA-256 for upstream copies.
  - Discuss new bundled datasets in an issue first. No JCR data (Clarivate forbids redistribution) and no scrapers.
  - To report a transcription error or a rights concern without a PR, use the "Data source concern" issue template.
- **Licence**: by contributing you agree that your contribution is licensed under AGPL-3.0-or-later and that you have the right to license it. Exception: third-party data in `data-sources/` is not covered by the AGPL, and its publishers keep all rights. Submitting such a file only confirms that it is an unmodified copy of the cited upstream file, or was compiled from the cited public source.
