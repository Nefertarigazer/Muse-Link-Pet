[English](CONTRIBUTING.md) | 中文

# 参与贡献 MuseLink

感谢为 MuseLink 贡献代码、文档与公共经验。开源仓库与个人部署使用完全一致的同一份代码，仅有站点配置与运行态凭据留在仓库之外（`config/` 模板均保持空白）。

## 1 开发流程

1. 修改代码或文档前，请先阅读 [RULES.zh.md](RULES.zh.md)。
2. 请在独立分支或工作树中开发，测试只在临时目录（`tests/`）和隔离预览端口进行，绝不触碰运行中的工具目录或生产信箱（见规则 E1）。
3. 严格遵守 `.gitattributes` 与 [RULES E10](RULES.zh.md#二工程规矩改这个项目的人和代理) 的换行与编码规范（Python/JS/JSON/MD/sh 用 LF；`.ps1` 用 CRLF，ASCII 或带 BOM 的 UTF-8）。

## 2 提交前校验

提交 PR 前请运行仓库检查及对应测试套件：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/check-syntax.ps1
python tools/check-i18n.py
python tools/check-docs.py
python tools/check-public.py
python -m unittest discover -s tests/pet -p "test_*.py" -v
python -m unittest discover -s tests/mailbox -p "test_*.py" -v
python -m unittest discover -s tests/explib -t tests/explib -v
node --test tests/pet-ui/*.test.mjs tests/pet/api.test.mjs tests/mailbox-ui/*.test.mjs
```

## 3 隐私与脱敏要求

每次提交的目录树都必须随时可公开发布（`tools/check-public.py` 返回 `0 findings`）。严禁提交：

- 真实姓名、个人邮箱、系统用户名、带真实用户名的路径（如 `C:\Users\<真实用户名>\...`、`/home/<真实用户名>/...`）或个人部署目录名。
- Tailnet IP（`100.x.y.z`）、个人服务器域名/主机名、SSH 私钥、执行器令牌或运行态数据（`muselink.json`、`muselink.site.json`、`user-avatar.png`、`seen.json`、`pet-history.jsonl`）。
- 未脱敏的生产信箱记录、含个人隐私的截图或未经确认的经验草稿。

## 4 文档与本地化规范

- 文档以英文为默认版本，按中英成对维护（`*.md` 或 `*.en.md` 配对 `*.zh.md`），首行必须提供中英文切换链接（`English | [中文](CONTRIBUTING.zh.md)` 与 `[English](CONTRIBUTING.md) | 中文`）。
- 同一对文档的二级标题（`##`）数量必须一致，所有相对链接和锚点均须有效（通过 `python tools/check-docs.py`）。
- 所有界面可见文案均应写入 `pc/i18n/{en,zh-Hans,zh-Hant}.json` 与 `vm/i18n/{en,zh-Hans,zh-Hant}.json`（通过 `python tools/check-i18n.py`）。

## 5 经验库投稿

向 `library/entries/` 提交经过验证的应用操作经验时：

- 本机确认的经验在累计满 5 条时弹卡统一询问分享一次；拒绝后将不再弹出分享询问。
- 每条分享条目须通过 `spec/experience.schema.json` 校验、`pc/explib/redact.py` 零命中脱敏检查及卡片预览长度预算。
- 请勿手工改动 `library/index.json` 哈希；提交分享的条目均按 [CC0 1.0](library/LICENSE) 贡献至公共领域。

## 6 开源许可与 DCO 声明

- 代码与文档采用 [Apache License 2.0](LICENSE) 许可（见 [NOTICE](NOTICE)）。
- `library/` 目录下的公共经验条目采用 [CC0 1.0](library/LICENSE) 释放至公共领域。
- 所有提交均须附带 [DCO 1.1](https://developercertificate.org/) 开发者原创声明签名（`git commit -s`）。
