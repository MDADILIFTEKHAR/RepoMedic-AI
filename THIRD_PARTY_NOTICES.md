# Third-Party Notices & Model Provenance

RepoMedic AI incorporates and interacts with open-weight artificial intelligence models and third-party software libraries. This document outlines the provenance, licensing, and attribution of all associated components.

---

## 1. Project Source Code License vs. Model Weight Licenses

* **RepoMedic AI Source Code**: Licensed under the [MIT License](LICENSE).
* **AI Model Weights**: Open-weight coding models utilized by this harness are distinct intellectual property subject to their respective upstream licenses. RepoMedic AI does not bundle or re-distribute model weight binaries directly; models are pulled on-demand via Ollama or accessed through hosted inference APIs.
* **Open Source vs. Open-Weight Clarification**: In accordance with open source definitions, models whose training data and training pipelines are not fully OSI-compliant are designated as **open-weight models** rather than OSI-approved open source, unless certified.

---

## 2. Model Provenance & Licensing

### A. Primary Recommended Model: Qwen2.5-Coder-7B-Instruct
* **Model Identifier**: `qwen2.5-coder:7b` (Ollama) / `Qwen/Qwen2.5-Coder-7B-Instruct` (Hugging Face)
* **Author / Provider**: Alibaba Cloud / Qwen Team
* **License**: **Apache License 2.0** (Permissive open-weights license allowing commercial use and adaptation)
* **Repository**: [https://github.com/QwenLM/Qwen2.5-Coder](https://github.com/QwenLM/Qwen2.5-Coder)
* **Capabilities**: 7-billion parameter dense model optimized for code understanding, diff generation, syntax parsing, and unit testing.

### B. Alternative Supported Model: DeepSeek-Coder-6.7B-Instruct
* **Model Identifier**: `deepseek-coder:6.7b` (Ollama) / `deepseek-ai/deepseek-coder-6.7b-instruct`
* **Author / Provider**: DeepSeek AI
* **License**: **DeepSeek License Agreement** (Permissive open-weights research & commercial license)
* **Repository**: [https://github.com/deepseek-ai/DeepSeek-Coder](https://github.com/deepseek-ai/DeepSeek-Coder)

---

## 3. Third-Party Python Runtime Dependencies

The following direct dependencies are installed and utilized by the application:

| Package | Upstream Version | License | Copyright / Maintainer |
| :--- | :--- | :--- | :--- |
| **FastAPI** | `>=0.115.0` | MIT License | Sebastián Ramírez (tiangolo) |
| **Uvicorn** | `>=0.32.0` | BSD-3-Clause | Encode OSS Ltd |
| **Pydantic** | `>=2.10.0` | MIT License | Samuel Colvin & Pydantic contributors |
| **Pydantic-Settings** | `>=2.7.0` | MIT License | Pydantic contributors |
| **HTTPX** | `>=0.28.0` | BSD-3-Clause | Encode OSS Ltd |
| **PyYAML** | `>=6.0.2` | MIT License | Kirill Simonov & YAML Project |
| **Jinja2** | `>=3.1.4` | BSD-3-Clause | Pallets Projects |
| **Python-Multipart** | `>=0.0.18` | Apache-2.0 | Andrew Chen Wang & contributors |
| **Pytest** | `>=8.3.0` | MIT License | Holger Krekel & Pytest Development Team |
| **Pytest-Asyncio** | `>=0.24.0` | Apache-2.0 | Tin Tvrtković & contributors |

All licenses are permissive and compatible with the MIT license of RepoMedic AI.
