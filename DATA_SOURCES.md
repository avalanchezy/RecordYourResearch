# 数据来源 / Data sources

本文件说明 RecordYourResearch 用到的分级数据从哪里来、为什么有些数据不随项目附带，以及怎样导入你自己有权使用的数据。英文摘要见[文末](#english-summary)。

> [!IMPORTANT]
> `data-sources/` 目录下的文件是根据公开发布的资料整理出的**事实性数据**，**不属于**本项目代码的 AGPL-3.0-or-later 授权范围，相关权利归原发布方所有。如果你是权利人并对收录有异议，请用[「数据来源异议」模板提一个 issue](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md)，我们会尽快移除。

## 随项目附带的数据

首次启动时，程序用下面两个文件在你的数据文件夹里生成本地分级库 `rankings/rankings.db`，所以 CCF 徽章和预警徽章开箱即用。

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
| 核对 | 转录内容与 [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 仓库（GPL-3.0）中的相应数据核对一致 |

- 名单依据公开通知整理；ShowJCR 只用作核对参照。
- 预警名单的含义和用法以中国科学院文献情报中心的官方说明为准。「⚠ 预警2025 / 2024」徽章只是提醒你留意，不是本项目对期刊的评价。
- 发现转录错误同样请用「数据来源异议」模板提 issue，附上对应的年份、刊名和官方通知。
- 本文件不在本项目的授权范围内。

## 不随项目附带的数据

下面这些是受许可保护的数据。本项目**不附带、不提供下载，也不会替你抓取**：

| 数据 | 为什么不附带 |
|---|---|
| JCR（Journal Citation Reports，影响因子与分区） | 由科睿唯安（Clarivate）提供，其条款禁止再分发 JCR 数据，也禁止用它建立衍生数据库。 |
| 中科院分区表（升级版） | 中国科学院文献情报中心面向订阅用户提供的数据库，不是公开数据。 |
| 新锐期刊分区表 | 发布方新锐学术（xr-scholar）的使用条款限制对数据的再利用。 |

如果你能通过合法途径使用这些数据（例如所在机构的订阅），可以把它们整理成 CSV 在本机导入。导入的数据只保存在你自己的数据文件夹里（`<数据目录>/rankings/rankings.db`）。请遵守数据提供方的许可条款，**不要**把这些 CSV 或生成的 `rankings.db` 提交到 Git、附在 issue / PR 里或分享给别人。

## 导入你自己的数据

```bash
python tools/build_rankings.py --cas FILE --xr FILE --jcr FILE
```

| 参数 | 说明 |
|---|---|
| `--cas FILE` | 中科院分区表（升级版）CSV |
| `--xr FILE` | 新锐期刊分区表 CSV |
| `--jcr FILE` | JCR CSV |
| `--cas-edition LABEL` | 中科院分区表的版本标签（如 `2025`）；不填时取 CSV 的「年份」列 |
| `--xr-edition LABEL` | 新锐期刊分区表的版本标签（如 `2026`）；不填时取 CSV 的「年份」列 |
| `--jcr-edition LABEL` | JCR 的版本标签（如 `2025`）；不填时取 `IF(<年份>)` 列名里的年份 |
| `--data-dir PATH` | 数据目录；默认是项目下的 `data/`，也可以用环境变量 `PAPER_TRACKER_DATA_DIR` 指定 |

- 用 start.bat / start.sh 安装的，请把命令里的 `python` 换成 `.venv\Scripts\python`（macOS / Linux 为 `.venv/bin/python`）。
- 三个数据参数都是可选的，按你拥有的数据提供即可；不带任何参数运行时，只用随项目附带的数据重建。
- **每次运行都会重新生成整个分级库**（随附的 CCF 目录和预警名单总会包含在内）。以后想追加一种数据，请把所有要用的 CSV 一起传入，否则之前导入的数据会被去掉。
- 导入后重启本程序，已建档稿件的徽章会按新数据重新匹配。
- 版本标签会显示在徽章的悬停提示里，例如「JCR 2025（你导入的数据，Clarivate）」。

### CSV 格式

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

Files under `data-sources/` contain factual data compiled from public sources. They are **not** covered by the project's AGPL-3.0-or-later licence, and all rights remain with the original publishers. Rights holders who object to their inclusion can [open an issue with the "Data source concern" template](https://github.com/avalanchezy/RecordYourResearch/issues/new?template=data_source.md) and we will remove the file. Please use the same template to report transcription errors.

**Included**

- `data-sources/ccf_2026.json`: the CCF list of recommended international conferences and journals, 7th edition (2026), © China Computer Federation (CCF). It was derived with `tools/parse_ccf.py` from the official PDF at <https://www.ccf.org.cn/Academic_Evaluation/By_category/> and has 681 entries (295 journals, 386 conferences). The official list prevails. CCF itself states that the list is not meant as a basis for academic evaluation.
- `data-sources/cas_warning_lists.json`: the international journal warning lists (2024 and 2025 editions) published in public notices by the National Science Library, Chinese Academy of Sciences (中国科学院文献情报中心). It contains facts only (journal name and reason). The transcription was cross-checked against the [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) repository (GPL-3.0).

**Not included**

- JCR (© Clarivate): its terms forbid redistribution and the creation of derivative databases.
- The CAS journal ranking, upgraded edition (中科院分区表): a subscriber database.
- The XR journal ranking (新锐期刊分区表, xr-scholar): its terms restrict reuse.

If you have legitimate access, import your own CSV files locally:

```bash
python tools/build_rankings.py --cas FILE --xr FILE --jcr FILE
```

- Each file is optional. Edition labels (`--cas-edition`, `--xr-edition`, `--jcr-edition`) are shown in badge tooltips; `--data-dir PATH` selects the data folder. If you installed with start.bat or start.sh, run the tool with `.venv\Scripts\python` (`.venv/bin/python` on macOS/Linux).
- Every run rebuilds the whole database, so pass all the CSV files you want to keep. Restart the app afterwards.
- Never commit, upload or share the CSV files or the generated `rankings.db`.
- CSV files need a header row and may be saved as UTF-8 (with or without BOM) or GBK, so Excel's 「CSV（逗号分隔）」 format on Chinese Windows works. Column names must match exactly; extra columns are ignored.
  - CAS: `Journal`, `ISSN/EISSN` (both numbers in one column, separated by `/`), `大类`, `大类分区`, `Top` are required; `Web of Science` and `年份` (edition) are optional.
  - XR: `Journal`, `大类中文名`, `大类新锐分区` are required; `ISSN`, `EISSN`, `Top`, `预警标记` and `年份` are optional.
  - JCR: `Journal`, `IF(<year>)` and `IF Quartile(<year>)_1` are required; `ISSN`, `EISSN` and `Category_1` are optional.
