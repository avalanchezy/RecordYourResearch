# 安全说明 / Security policy

[English](#english) 见下文。

## 报告漏洞

**请不要在公开 issue 里报告安全问题。** 请使用 GitHub 的私密漏洞报告：

<https://github.com/avalanchezy/RecordYourResearch/security/advisories/new>

如果上面的链接打不开，请开一个只写「需要私下报告安全问题」、不含任何细节的 issue，维护者会联系你。（可以任选一个 issue 模板，把模板内容换成这一句。）

报告里请写明：

- 版本（或 commit）、操作系统、Python 版本、浏览器；
- 尽量精简的复现步骤，以及可能造成的影响；
- 是否依赖特殊配置（例如 `--host 0.0.0.0`）。

> [!WARNING]
> **不要附上** `tracker.db`、备份文件、PDF、真实的 OpenReview 账号或密码、稿件编号、邮箱，或未打码的截图。请用全新的数据目录和虚构的演示数据复现。

维护者会尽快确认收到，并和你商量修复与公开的时间。这是业余维护的项目，无法保证响应时间。安全修复只针对最新发布的版本。

## 设计：单用户、只在本机运行

- 程序默认只监听 `127.0.0.1:8765`。
- **没有登录和用户体系。** 能连上这个端口，就能读写你的全部数据（投稿信息、PDF、时间线），还能用你绑定的 OpenReview 账号发起同步。因此：
  - 不要用 `--host 0.0.0.0`（或任何非本机地址）在你不信任的网络上开放；
  - 不要通过端口转发、内网穿透或反向代理把它暴露到网上；
  - 不要把它部署到服务器上给别人用；
  - 在多人共用的电脑上，其他系统用户同样能访问本机的 `127.0.0.1` 端口，请只在你自己的电脑上运行。

### 内置的防护

- **Host 检查**：只接受 Host 为 `127.0.0.1`、`localhost` 或 `[::1]` 的请求，防止 DNS rebinding（恶意网页把自己的域名解析到 127.0.0.1 来读你的数据）。
- **Origin 检查**：带 `Origin` 头的写请求（POST、PATCH、DELETE 等）必须与 Host 同源，否则拒绝，防止其他网站跨站提交（CSRF）。
- **防嵌入与其他响应头**：`X-Frame-Options: DENY`、`Content-Security-Policy: frame-ancestors 'none'`（防点击劫持），以及 `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。
- PDF 只从本实例的 `pdfs/` 目录读取，上传文件的原始文件名不参与存储路径；外部链接只允许 `http` / `https`。
- FastAPI 自带的 API 调试页面（`/docs`、`/redoc`）和 `/openapi.json` 是特意关闭的：调试页会从 CDN 加载脚本，日常使用也用不到。

这些措施防的是「浏览器里打开的恶意网页」，**不是身份验证**：网络上的其他机器可以随意伪造 Host 头，所以一旦监听了非本机地址，这些检查就挡不住它们。

## 凭据存储

| 系统 | OpenReview 密码的保存方式 |
|---|---|
| Windows | DPAPI 加密，密文存在 `tracker.db` 里；只有同一台电脑上的同一个 Windows 用户能解密 |
| macOS | 系统钥匙串（通过 `keyring`），数据库里只存一个引用标记；每个数据目录各用一个钥匙串条目 |
| Linux | 桌面环境提供的 Secret Service（如 GNOME Keyring，通过 `keyring`），数据库里只存一个引用标记；每个数据目录各用一个钥匙串条目 |
| 没有可用的安全存储 | **拒绝保存**，账号不会被绑定 |

- 密码只发送给 OpenReview 官方 API 用于登录；登录令牌只缓存在内存里，不写盘。
- 解除绑定会删除数据库里的账号记录和钥匙串里的密码，并清理数据目录 `backups/` 下备份文件里的账号记录。

## 哪些数据会离开本机

| 去向 | 什么时候 | 发送什么 |
|---|---|---|
| OpenReview 官方 API（`api2.openreview.net`、`api.openreview.net`） | 你绑定了账号之后：绑定、同步、查询，以及每日后台同步 | 登录邮箱和密码（仅登录时）、登录令牌、查询参数（你的 profile ID、稿件 forum ID，按标题匹配时还有稿件标题） |
| 爱思唯尔中国的稿件查询服务（`dsp-api.elsevier.cn`） | 只有在你开启「爱思唯尔一键查稿（实验）」之后：点击查询时，或点「拉取期刊缩写字典」（只在功能开启时显示）时 | 查询：稿件编号、通讯作者姓名拼音、期刊缩写和随机设备标识。拉取字典：只发送随机设备标识。设备标识在第一次使用时随机生成，保存在数据目录的 `elsevier_device_id` 文件里 |

- 「从投稿系统导入」**不发送任何数据**：你粘贴的页面文字只在本机解析。程序不会登录或访问这些投稿系统（IEEE、Springer、Elsevier、MDPI 等），也不保存它们的密码。
- 所有请求都使用如实的 User-Agent（`RecordYourResearch/<版本>` 加项目地址）。
- 没有遥测、统计、崩溃上报或自动更新检查；网页不从 CDN 加载任何资源。
- 抽屉里的外部链接只有在你点击时才由浏览器打开。

## 本地数据

- `tracker.db`、PDF 和备份在磁盘上**没有加密**（OpenReview 密码除外）。请使用系统磁盘加密（BitLocker、FileVault、LUKS 等）。
- 如果把数据目录放在 OneDrive、Dropbox、iCloud 等同步盘里，你的稿件和数据库会被同步到相应的云服务。
- 导出的 CSV 包含你的投稿信息，分享前请检查。
- 只解析你信任的 PDF（通常就是你自己的稿件），并保持 PyMuPDF 等依赖为最新版本。

## 范围

属于本项目的安全问题，例如：

- 绕过 Host / Origin 检查，让恶意网页读取或修改数据；
- 界面里的 XSS（例如通过稿件标题、粘贴的官方页面或投稿系统页面，或 OpenReview 返回的数据注入脚本）；
- 路径穿越、任意文件读写；
- 凭据泄露（例如密码被明文写进数据库、日志或备份）；
- 未经用户开启的对外网络请求。

不属于本项目的问题：

- 你主动监听非本机地址（如 `--host 0.0.0.0`）后，同一网络里的人访问到了数据，这是已知的设计限制；
- 已经能以你的系统用户身份运行代码或读取你文件的攻击者；
- OpenReview、爱思唯尔等第三方服务本身的问题，请向它们报告；
- 依赖项本身的漏洞请向上游报告。如果本项目的用法让问题更严重，也欢迎告诉我们。

---

## English

### Reporting a vulnerability

**Please do not report security issues in public issues.** Use GitHub's private vulnerability reporting:

<https://github.com/avalanchezy/RecordYourResearch/security/advisories/new>

If the link does not work, open an issue that only says you need to report a security problem privately (no details); the maintainers will contact you. You can pick any issue template and replace its content with that one sentence.

Include the version or commit, your OS, Python version and browser, minimal steps to reproduce, the impact, and any special configuration (such as `--host 0.0.0.0`).

> [!WARNING]
> **Never attach** `tracker.db`, backups, PDFs, real OpenReview credentials, manuscript numbers, emails or unredacted screenshots. Reproduce with a fresh data folder and made-up demo data.

The maintainers will acknowledge your report as soon as they can and agree on a timeline for the fix and disclosure with you. This is a volunteer project, so response times cannot be guaranteed. Only the latest release receives security fixes.

### Design: single user, local only

- The app binds to `127.0.0.1:8765` by default.
- **There is no login.** Anyone who can reach the port can read and change all your data and trigger syncs with your bound OpenReview account. So never bind to `0.0.0.0` or any non-local address on a network you don't trust. Don't expose the app through port forwarding, tunnels or reverse proxies, and don't host it for other people. On a shared computer, other OS users can also reach `127.0.0.1`.
- **Built-in protections**:
  - a Host check (only `127.0.0.1`, `localhost` and `[::1]`) against DNS rebinding;
  - an Origin check that rejects cross-site write requests (CSRF);
  - anti-framing headers (`X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`), plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`;
  - PDFs are served only from the instance's `pdfs/` folder;
  - external links must be `http`/`https`;
  - FastAPI's API debug pages (`/docs`, `/redoc`) and `/openapi.json` are turned off on purpose, because the debug pages load scripts from a CDN and the app doesn't need them.

  These protect you against malicious web pages in your browser. They are **not authentication**: other machines on a network can forge the Host header.

### Credential storage

- **Windows**: DPAPI encryption; the ciphertext in `tracker.db` can only be decrypted by the same Windows user on the same machine.
- **macOS / Linux**: the system Keychain or Secret Service via `keyring`; the database stores only a reference. Each data folder uses its own keychain entry.
- **No secure store available**: the app refuses to store the password.

The password is sent only to the official OpenReview API to log in. The login token is cached in memory only. Unbinding deletes the account record, the keychain entry and the account records inside the backup files.

### What leaves your machine

- **The official OpenReview API** (`api2.openreview.net`, `api.openreview.net`), only once you bind an account. The app sends your login email and password (at login only), the token and query parameters: your profile id, forum ids and, for title matching, the paper title.
- **Elsevier China's manuscript-query service** (`dsp-api.elsevier.cn`), only after you enable the experimental one-click query. A query you click sends the manuscript number, the corresponding author's pinyin, the journal acronym and a random device id. The 「拉取期刊缩写字典」 (journal-acronym dictionary) button, shown only while the feature is on, sends only the random device id. The id is generated on first use and stored as `elsevier_device_id` in the data folder.

**Import from a submission system sends nothing.** The page text you paste is parsed locally. The app never logs in to or contacts those submission systems (IEEE, Springer, Elsevier, MDPI and others) and does not store their passwords.

All requests use an honest User-Agent. There is no telemetry, analytics, crash reporting or update check, and nothing is loaded from a CDN.

### Local data

`tracker.db`, PDFs and backups are not encrypted at rest; only the password is protected. Use full-disk encryption, and remember that a data folder inside a cloud-synced directory is uploaded to that cloud. Exported CSV files contain your submission data. Only parse PDFs you trust and keep dependencies such as PyMuPDF up to date.

### Scope

**In scope**:

- bypassing the Host/Origin checks;
- XSS in the UI, including through pasted submission-system pages;
- path traversal or arbitrary file access;
- credential leaks;
- outbound requests the user did not enable.

**Out of scope**:

- access by other people after you bound the app to a non-local address (a documented limitation);
- attackers who can already run code as your OS user;
- issues in third-party services such as OpenReview or Elsevier;
- vulnerabilities in dependencies, which should be reported upstream. Tell us if the way we use a dependency makes a problem worse.
