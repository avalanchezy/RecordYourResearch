# 数据来源 / Data sources

本文件说明 RecordYourResearch 随附的分级数据从哪里来、怎样校验，以及怎样导入或替换数据。英文摘要见[文末](#english-summary)。

> [!IMPORTANT]
> `data-sources/` 目录下的数据来自各自的原发布方，**权利归原发布方所有**，**不属于**本项目代码的 AGPL-3.0-or-later 授权范围；随项目附带不代表发布方认可本项目。其中 `ccf_2026.json`、`cas_warning_lists.json` 是本项目依据官方 PDF 和公开通知整理的转录，`cas_2025.csv`、`xr_2026.csv` 是 ShowJCR 公开镜像中文件未经修改的原样副本。如果你是权利人并对收录有异议，请用[「数据来源异议」模板提一个 issue](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md)，我们会尽快移除相应文件。

## 随项目附带的数据

首次启动时，程序用下面四个文件在你的数据文件夹里生成本地分级库 `rankings/rankings.db`（需要几秒钟），所以 CCF、中科院分区、新锐分区和预警徽章都开箱即用、不需要联网。

| 文件 | 内容 | 发布方 | 怎么来的 |
|---|---|---|---|
| `ccf_2026.json` | CCF 推荐国际学术会议和期刊目录（第七版，2026） | 中国计算机学会（CCF） | 本项目从官方 PDF 整理 |
| `cas_warning_lists.json` | 国际期刊预警名单（2024 版、2025 版） | 中国科学院文献情报中心 | 本项目依据公开通知整理 |
| `cas_2025.csv` | 期刊分区表（升级版）2025 | 中国科学院文献情报中心 | [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 公开镜像中文件的原样副本 |
| `xr_2026.csv` | 新锐期刊分区表 2026 | xr-scholar.com | [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 公开镜像中文件的原样副本 |

随项目附带中科院分区表和新锐期刊分区表，是为了让研究者离线、开箱即用地看到徽章。这两份文件是 ShowJCR 公开镜像中对应文件未经修改的原样副本，权利归原发布方所有。

### `data-sources/ccf_2026.json`：CCF 推荐目录

| 项目 | 内容 |
|---|---|
| 名称 | 中国计算机学会推荐国际学术会议和期刊目录（第七版，2026） |
| 发布方与版权 | © 中国计算机学会（CCF） |
| 来源 | CCF 官网发布的目录 PDF：<https://www.ccf.org.cn/Academic_Evaluation/By_category/> |
| 版本 | 正式版，2026-03-31 发布、2026-04-09 勘误更新（文件内 `version`、`released` 字段） |
| 整理日期 | 2026-11-17（文件内 `retrieved` 字段） |
| 规模 | 681 条：期刊 295、会议 386；10 个领域；A 类 95、B 类 245、C 类 341 |
| 字段 | `field` 领域、`type`（`journal` / `conference`）、`rank`（A / B / C）、`seq` 目录内序号、`abbr` 简称、`name` 全称、`publisher` 出版社、`url` 目录中列出的网址 |
| 生成方式 | 用 `tools/parse_ccf.py` 从官方 PDF 解析生成；本项目不附带 PDF 原件 |

- 本文件只是把官方目录转成机器可读的格式，方便在本机匹配徽章，不代表 CCF 认可本项目。
- **CCF 自己也说明，推荐目录不应被当作学术评价的依据。** 本项目显示的 CCF 徽章仅供参考。
- 一切以 CCF 官方发布的目录为准。发现转录错误请用[「数据来源异议」模板](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md)提 issue，附上官方目录中对应的领域、类别和序号。
- 本文件不在本项目的授权范围内；在其他场合使用这些数据，请遵守 CCF 的相关规定。

### `data-sources/cas_warning_lists.json`：中科院国际期刊预警名单

| 项目 | 内容 |
|---|---|
| 名称 | 国际期刊预警名单（2024 版、2025 版） |
| 发布方 | 中国科学院文献情报中心（期刊分区表团队） |
| 来源 | 各年度公开发布的预警名单通知 |
| 内容 | 只收录刊名与预警原因这类事实信息：2025 版 5 种期刊、2024 版 24 种期刊 |
| 核对 | 转录内容与 [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 仓库中的相应数据核对一致 |

- 名单依据公开通知整理；ShowJCR 只用作核对参照。
- 预警名单的含义和用法以中国科学院文献情报中心的官方说明为准。「⚠ 预警2025 / 2024」徽章只是提醒你留意，不是本项目对期刊的评价。
- 发现转录错误同样请用「数据来源异议」模板提 issue，附上对应的年份、刊名和官方通知。
- 本文件不在本项目的授权范围内。

### `data-sources/cas_2025.csv`：中科院期刊分区表（升级版）2025

| 项目 | 内容 |
|---|---|
| 名称 | 中科院文献情报中心期刊分区表（升级版）2025 |
| 发布方 | 中国科学院文献情报中心 |
| 来源 | [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 仓库中 `中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv` 的原样副本，逐字节一致，取自 commit `4687a8f`（2026-09-23）；ShowJCR 注明的来源是 advanced.fenqubiao.com |
| 规模 | 21,772 种期刊 |
| 格式 | UTF-8（无 BOM），LF 换行，第一行是表头 |
| SHA-256 | `481224dca2cacc1cce49afb44c968d734c834e498e5ff054f15efb5a548789fc` |

- 本项目没有修改这份文件；生成分级库时只用到其中的刊名、ISSN / EISSN、大类、大类分区、Top、Web of Science 和年份几列。
- 据 [IT之家报道](https://www.ithome.com/0/934/100.htm)，中科院文献情报中心于 2026-03-27 宣布自 2026 年起停止更新期刊分区表，2025 版是最后一版。
- 一切以中国科学院文献情报中心发布的官方数据为准；徽章仅供参考。
- 权利归中国科学院文献情报中心所有，本文件不在本项目的授权范围内。

### `data-sources/xr_2026.csv`：新锐期刊分区表 2026

| 项目 | 内容 |
|---|---|
| 名称 | 新锐期刊分区表 2026 |
| 发布方 | xr-scholar.com |
| 版本 | 2026 版，2026-03-24 发布 |
| 来源 | [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 仓库中 `中科院分区表及JCR原始数据文件/XR2026-UTF8.csv` 的原样副本，逐字节一致，取自同一 commit `4687a8f` |
| 规模 | 22,299 种期刊 |
| 格式 | UTF-8（无 BOM），LF 换行，第一行是表头 |
| SHA-256 | `8bffe4bdcae8f710c756e0138d93219bb53555eae23827c20287bc9200a027e6` |

- 本项目没有修改这份文件。「新锐预警」徽章来自其中的「预警标记」列。
- ShowJCR 的同一目录里另有一份 `XR2026Conferences-UTF8.csv`（15 种计算机领域的会议论文集），本项目**没有**附带。
- 一切以 xr-scholar.com 发布的官方数据为准；徽章仅供参考。
- 权利归发布方所有，本文件不在本项目的授权范围内。

### 校验：`data-sources/SHA256SUMS`

`SHA256SUMS` 只记录两份上游原样副本 `cas_2025.csv` 和 `xr_2026.csv` 的 SHA-256。这两个 CSV 和 `SHA256SUMS` 本身按字节原样保存：`.gitattributes` 里对 `data-sources/*.csv` 和 `data-sources/SHA256SUMS` 设置了 `-text`，Git 不做换行转换，所以在任何系统上检出，校验值都一样。两个 JSON 文件是本项目自己整理的数据，按普通文本处理，不在 `SHA256SUMS` 里。测试会检查 `SHA256SUMS` 正好列出 `data-sources/` 里的全部 CSV 文件。

```bash
cd data-sources
sha256sum -c SHA256SUMS            # Linux，或 Windows 上的 Git Bash
shasum -a 256 -c SHA256SUMS        # macOS
```

Windows PowerShell 里也可以逐个计算后与 `SHA256SUMS` 比对（输出是大写十六进制，比对时忽略大小写）：

```powershell
Get-FileHash data-sources\cas_2025.csv -Algorithm SHA256
```

## 不随项目附带的数据：JCR

| 数据 | 为什么不附带 |
|---|---|
| JCR（Journal Citation Reports，影响因子与分区） | 由科睿唯安（Clarivate）提供，其条款禁止再分发 JCR 数据，也禁止用它建立衍生数据库。 |

本项目不附带 JCR、不提供下载，也不会替你抓取。如果你有使用授权（例如所在机构的订阅），可以把它整理成 CSV 在本机导入（见下文）。导入的数据只保存在你自己的数据文件夹里（`<数据目录>/rankings/rankings.db`）。请遵守 Clarivate 的许可条款，**不要**把 JCR 的 CSV 或含有 JCR 的 `rankings.db` 提交到 Git、附在 issue / PR 里或分享给别人。

## 导入或替换数据

```bash
python tools/build_rankings.py --cas FILE --xr FILE --jcr FILE
```

| 参数 | 说明 |
|---|---|
| （不带参数） | 只用随附的四份数据重建分级库 |
| `--cas FILE` | 用你的 CSV 替换随附的中科院分区表 |
| `--xr FILE` | 用你的 CSV 替换随附的新锐期刊分区表（例如更新的版本） |
| `--jcr FILE` | 加入 JCR |
| `--cas-edition LABEL` | 中科院分区表的版本标签（如 `2025`）；不填时取 CSV 的「年份」列 |
| `--xr-edition LABEL` | 新锐期刊分区表的版本标签（如 `2026`）；不填时取 CSV 的「年份」列 |
| `--jcr-edition LABEL` | JCR 的版本标签（如 `2025`）；不填时取 `IF(<年份>)` 列名里的年份 |
| `--data-dir PATH` | 数据目录；默认是项目下的 `data/`，也可以用环境变量 `PAPER_TRACKER_DATA_DIR` 指定 |

- 用 start.bat / start.sh 安装的，请把命令里的 `python` 换成 `.venv\Scripts\python`（macOS / Linux 为 `.venv/bin/python`）。
- 几个参数可以一起用。**每次运行都会重新生成整个分级库**：随附的 CCF 目录和预警名单总会包含在内，没传 `--cas` / `--xr` 时用随附的中科院 / 新锐表。以后再运行时，请把所有要用的 CSV 一起传入，否则之前导入的数据会被去掉。
- 导入后重启本程序，已建档稿件的徽章会按新数据重新匹配。
- 徽章的悬停提示标明来源和版本。随附数据的格式是「中科院分区表升级版 2025（中科院文献情报中心）· 大类 · WoS」「新锐期刊分区表 2026（xr-scholar.com）· 大类」；你导入的数据会标「（你导入的数据）」，例如「JCR 2025（你导入的数据，Clarivate）」。
- 以后的版本更新随附数据时，启动会自动重建分级库。如果你导入过自己的 CSV，程序会保留你的分级库并在日志里提示，「渠道查询」页的「分级速查」卡片上也会提示你带上自己的 CSV 重新运行 `tools/build_rankings.py`。

### CSV 格式

替换用的中科院 / 新锐 CSV 和 JCR CSV 用下面的格式；随附的 `cas_2025.csv`、`xr_2026.csv` 就是这种格式。

- 第一行是表头。编码可以是 UTF-8（带不带 BOM 都可以）或 GBK：中文 Windows 上用 Excel 另存为「CSV（逗号分隔）」得到的就是 GBK，可以直接导入。
- 列名必须与下表**完全一致**；多余的列会被忽略。
- 同一种期刊按刊名合并（忽略大小写和标点）；同一 ISSN 被拆成多行时，缺的部分会自动从兄弟行补齐。
- 分区列只要以分区数字开头即可，如 `2` 或 `2 [123/456]`。

**中科院分区表（`--cas`）**

| 列 | 必需 | 说明 |
|---|---|---|
| `Journal` | 是 | 刊名 |
| `ISSN/EISSN` | 是 | ISSN 与 EISSN 写在同一列，用 `/` 分隔 |
| `大类` | 是 | 大类学科 |
| `大类分区` | 是 | 1–4 |
| `Top` | 是 | `是` 表示 Top 期刊 |
| `Web of Science` | 否 | 收录情况，如 `SCIE` |
| `年份` | 否 | 没给 `--cas-edition` 时用作版本标签 |

**新锐期刊分区表（`--xr`）**

| 列 | 必需 | 说明 |
|---|---|---|
| `Journal` | 是 | 刊名 |
| `ISSN` | 否 | |
| `EISSN` | 否 | |
| `大类中文名` | 是 | 大类学科 |
| `大类新锐分区` | 是 | 1–4 |
| `Top` | 否 | `是` 或 `Top` 表示 Top 期刊 |
| `预警标记` | 否 | 原样显示在徽章的悬停提示里 |
| `年份` | 否 | 没给 `--xr-edition` 时用作版本标签 |

**JCR（`--jcr`）**

| 列 | 必需 | 说明 |
|---|---|---|
| `Journal` | 是 | 刊名 |
| `ISSN` | 否 | |
| `EISSN` | 否 | |
| `IF(<year>)` | 是 | 影响因子，`<year>` 为 JCR 年份，如 `IF(2025)` |
| `IF Quartile(<year>)_1` | 是 | 分区 `Q1`–`Q4`，如 `IF Quartile(2025)_1` |
| `Category_1` | 否 | 学科类别 |

示例（虚构数据，仅演示格式）：

```csv
Journal,ISSN/EISSN,大类,大类分区,Top,Web of Science,年份
Demo Journal of Examples,0000-0000/0000-0000,计算机科学,2,否,SCIE,2025
```

```csv
Journal,ISSN,EISSN,大类中文名,大类新锐分区,Top,预警标记,年份
Demo Journal of Examples,0000-0000,0000-0000,计算机科学,2,,,2026
```

```csv
Journal,ISSN,EISSN,IF(2025),IF Quartile(2025)_1,Category_1
Demo Journal of Examples,0000-0000,0000-0000,1.0,Q3,COMPUTER SCIENCE
```

## English summary

The data under `data-sources/` comes from its original publishers, who keep all rights. It is **not** covered by the project's AGPL-3.0-or-later licence, and bundling it does not mean the publishers endorse this project. `ccf_2026.json` and `cas_warning_lists.json` are this project's own transcriptions of the official PDF and public notices; `cas_2025.csv` and `xr_2026.csv` are unmodified copies of files in the public ShowJCR mirror. Rights holders who object to a file can [open an issue with the "Data source concern" template](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md), and we will remove the file promptly. Please use the same template to report transcription errors.

**Bundled** (the first start builds the local ranking database from these four files in a few seconds, so CCF, CAS, XR and warning badges work offline, out of the box; JCR badges need your own import):

- `ccf_2026.json`: the CCF list of recommended international conferences and journals, 7th edition (2026), © China Computer Federation (CCF).
  - Derived with `tools/parse_ccf.py` from the official PDF at <https://www.ccf.org.cn/Academic_Evaluation/By_category/>.
  - 681 entries: 295 journals and 386 conferences.
  - The official list prevails. CCF itself states that the list is not meant as a basis for academic evaluation.
- `cas_warning_lists.json`: the international journal warning lists (2024 and 2025 editions) published in public notices by the National Science Library, Chinese Academy of Sciences (中国科学院文献情报中心).
  - Facts only: journal name and reason.
  - The transcription was cross-checked against the [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) repository.
- `cas_2025.csv`: the CAS journal ranking (中科院文献情报中心期刊分区表), upgraded edition 2025, published by the National Science Library, CAS.
  - A byte-identical, unmodified copy of `中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv` in hitfyd/ShowJCR, commit `4687a8f` (2026-09-23). ShowJCR names advanced.fenqubiao.com as its source.
  - 21,772 journals; UTF-8 without BOM, LF line endings.
  - SHA-256 `481224dca2cacc1cce49afb44c968d734c834e498e5ff054f15efb5a548789fc`.
  - According to a [report by IT之家 (ITHome)](https://www.ithome.com/0/934/100.htm), the National Science Library of CAS announced on 2026-03-27 that it stops updating the table from 2026, so the 2025 edition is the last one.
- `xr_2026.csv`: the XR journal ranking (新锐期刊分区表) 2026 from xr-scholar.com, edition released 2026-03-24.
  - A byte-identical, unmodified copy of `中科院分区表及JCR原始数据文件/XR2026-UTF8.csv` in hitfyd/ShowJCR, same commit.
  - 22,299 journals; UTF-8 without BOM, LF line endings.
  - SHA-256 `8bffe4bdcae8f710c756e0138d93219bb53555eae23827c20287bc9200a027e6`.
  - `XR2026Conferences-UTF8.csv` (15 CS conference proceedings), in the same ShowJCR folder, is **not** included.

We bundle the CAS and XR tables as unmodified copies of ShowJCR's public mirror so that researchers get badges offline, out of the box. The publishers' official data always prevails, and badges are for reference only.

**Checksums**: `data-sources/SHA256SUMS` covers only the two upstream copies, `cas_2025.csv` and `xr_2026.csv`. Those two files and `SHA256SUMS` itself are stored byte-exact: `.gitattributes` sets `-text` on `data-sources/*.csv` and `data-sources/SHA256SUMS`, so Git never converts their line endings. The two JSON files are our own data and get normal text handling. A test checks that `SHA256SUMS` lists exactly the CSV files in `data-sources/`. To verify, run `cd data-sources && sha256sum -c SHA256SUMS` (Linux, or Git Bash on Windows) or `shasum -a 256 -c SHA256SUMS` (macOS). You can also run `Get-FileHash <file> -Algorithm SHA256` in PowerShell and compare case-insensitively.

**Not bundled**: JCR (© Clarivate). Its terms forbid redistribution and the creation of derivative databases. If you have a licence, import it locally and never commit, upload or share the CSV or a `rankings.db` that contains it.

**Importing or replacing data**:

```bash
python tools/build_rankings.py --cas FILE --xr FILE --jcr FILE
```

- With no arguments the tool rebuilds the database from the bundled data. `--cas FILE` and `--xr FILE` replace the bundled table (for example with a newer XR edition), and `--jcr FILE` adds JCR.
- Edition labels (`--cas-edition`, `--xr-edition`, `--jcr-edition`) appear in badge tooltips. Without them, the edition comes from the CSV's 年份 column (CAS, XR) or the `IF(<year>)` header (JCR). `--data-dir PATH` selects the data folder.
- If you installed with start.bat or start.sh, run the tool with `.venv\Scripts\python` (`.venv/bin/python` on macOS/Linux).
- Every run rebuilds the whole database, so pass all the CSV files you want to keep, then restart the app.
- Tooltips read 「中科院分区表升级版 2025（中科院文献情报中心）· 大类 · WoS」 and 「新锐期刊分区表 2026（xr-scholar.com）· 大类」 for bundled data. Data you imported is marked 「（你导入的数据）」.
- When a later version ships updated bundled data, startup rebuilds the database automatically. If you imported your own CSVs, the app keeps your database and logs a message, and the 分级速查 card on the 渠道查询 page tells you to rerun `tools/build_rankings.py` with your CSVs.
- CSV files need a header row and may be saved as UTF-8 (with or without BOM) or GBK, so Excel's 「CSV（逗号分隔）」 format on Chinese Windows works. Column names must match exactly; extra columns are ignored.
  - CAS: `Journal`, `ISSN/EISSN` (both numbers in one column, separated by `/`), `大类`, `大类分区`, `Top` are required; `Web of Science` and `年份` (edition) are optional.
  - XR: `Journal`, `大类中文名`, `大类新锐分区` are required; `ISSN`, `EISSN`, `Top`, `预警标记` and `年份` are optional.
  - JCR: `Journal`, `IF(<year>)` and `IF Quartile(<year>)_1` are required; `ISSN`, `EISSN` and `Category_1` are optional.
