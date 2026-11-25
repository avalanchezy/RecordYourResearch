# Third-party notices / 第三方声明

RecordYourResearch is licensed under the GNU Affero General Public License v3.0 or later ([LICENSE](LICENSE)). This file lists the third-party software it uses and their licences.

本项目以 AGPL-3.0-or-later 发布。下面列出项目用到的第三方软件及其许可证。**除 Vue 之外，没有任何依赖被复制进本仓库**：其余依赖都由 `pip install -r requirements.txt` 从 PyPI 安装，它们的许可证文本随各自的安装包分发。

## 1. Bundled software

### Vue.js 3.4.38

- File: `web/vendor/vue.global.prod.js`, the unmodified production global build of Vue 3.4.38
- Project: <https://vuejs.org/>, source: <https://github.com/vuejs/core>
- Licence: MIT

```text
The MIT License (MIT)

Copyright (c) 2018-present, Yuxi (Evan) You and Vue contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## 2. Runtime dependencies (installed from PyPI, not bundled)

| Package | Licence | Used for |
|---|---|---|
| [PyMuPDF](https://pypi.org/project/PyMuPDF/) | AGPL-3.0, or an Artifex commercial licence | PDF parsing (`app/parser.py`, `tools/parse_ccf.py`) |
| [FastAPI](https://pypi.org/project/fastapi/) | MIT | Web framework |
| [Starlette](https://pypi.org/project/starlette/) | BSD-3-Clause | ASGI toolkit (FastAPI dependency) |
| [uvicorn](https://pypi.org/project/uvicorn/) | BSD-3-Clause | ASGI server |
| [httpx](https://pypi.org/project/httpx/) / [httpcore](https://pypi.org/project/httpcore/) | BSD-3-Clause | HTTP client for OpenReview and the optional Elsevier query |
| [python-multipart](https://pypi.org/project/python-multipart/) | Apache-2.0 | PDF upload (form parsing) |
| [pydantic](https://pypi.org/project/pydantic/) | MIT | Request validation (FastAPI dependency) |
| [keyring](https://pypi.org/project/keyring/) | MIT | macOS Keychain / Linux Secret Service; non-Windows systems only |
| [certifi](https://pypi.org/project/certifi/) | MPL-2.0 | CA certificate bundle (httpx dependency) |
| [anyio](https://pypi.org/project/anyio/) / [h11](https://pypi.org/project/h11/) | MIT | Async I/O and HTTP/1.1 (Starlette, uvicorn and httpcore dependencies) |
| [idna](https://pypi.org/project/idna/) | BSD-3-Clause | Internationalised domain names (httpx dependency) |
| [click](https://pypi.org/project/click/) | BSD-3-Clause | Command-line handling (uvicorn dependency) |

Notes:

- **PyMuPDF** is dual-licensed by Artifex Software under the AGPL-3.0 or a commercial licence. Because RecordYourResearch depends on it, the project as a whole is distributed under AGPL-3.0-or-later.
- These packages pull in further small dependencies (for example `typing-extensions`, `sniffio`, `annotated-types` and `pydantic-core`, plus the platform back ends of `keyring`). Their licences ship with the installed packages. To see what is installed in your environment, run `pip list`, and `pip show <package>` for a package's licence.
- Licences are listed for the versions current at the time of writing. If a package changes its licence, the licence shipped with the installed version applies.

### Optional: OCR add-on (`requirements-ocr.txt`, installed only if you choose to)

| Package | Licence | Used for |
|---|---|---|
| [RapidOCR](https://pypi.org/project/rapidocr/) | Apache-2.0 | Screenshot / scanned-PDF text recognition; ships PaddleOCR models (Apache-2.0) converted to ONNX |
| [ONNX Runtime](https://pypi.org/project/onnxruntime/) | MIT | Runs the OCR models on the CPU |

RapidOCR in turn installs OpenCV (`opencv-python-headless`, Apache-2.0), NumPy, Shapely, pyclipper, Pillow and a few small helpers; their licences ship with the installed packages.

## 3. Development and test dependencies

Packages in `requirements-dev.txt` (for example pytest) are only used to develop and test the project. They are not part of the application.

## 4. Data files

The files under `data-sources/` are data, not software. They are **not** covered by the AGPL, and their publishers keep all rights.

| File | Content | Publisher |
|---|---|---|
| `ccf_2026.json` | CCF list of recommended international conferences and journals, 7th edition (2026), compiled by this project from the official PDF | China Computer Federation (CCF) |
| `cas_warning_lists.json` | International journal warning lists, 2024 and 2025 editions, compiled from public notices | National Science Library, Chinese Academy of Sciences |
| `cas_2025.csv` | CAS journal ranking (中科院分区表), upgraded edition 2025: an unmodified copy of a file in the public hitfyd/ShowJCR mirror | National Science Library, Chinese Academy of Sciences |
| `xr_2026.csv` | XR journal ranking (新锐期刊分区表) 2026: an unmodified copy of a file in the public hitfyd/ShowJCR mirror | xr-scholar.com |

`data-sources/SHA256SUMS` holds the checksums of the two unmodified copies, `cas_2025.csv` and `xr_2026.csv`. See [DATA_SOURCES.md](DATA_SOURCES.md) for provenance and editions, and for how rights holders can ask for a file to be removed.

## 5. Trademarks

OpenReview, Elsevier (爱思唯尔), Editorial Manager, ScholarOne, Clarivate, Journal Citation Reports (JCR), CCF, 中科院分区表, 新锐期刊分区表 and the other names mentioned in this project are trademarks or names of their respective owners. They are used only to describe compatibility. The project includes no logos of these organisations and is not affiliated with them.
