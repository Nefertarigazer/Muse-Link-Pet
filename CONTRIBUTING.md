English | [中文](CONTRIBUTING.zh.md)

# Contributing to MuseLink

Thank you for helping improve MuseLink. The open-source repository and personal deployments share the exact same codebase; only deployment-specific configuration and runtime secrets stay outside the repository (`config/` templates remain blank).

## 1 Development workflow

1. Read [RULES.en.md](RULES.en.md) before changing code or documentation.
2. Develop in a dedicated branch or worktree and test only against temporary directories (`tests/`) and isolated preview ports—never touch live tool directories or production mailboxes (RULE E1).
3. Keep line endings and file encodings compliant with `.gitattributes` and [RULES E10](RULES.en.md#2-engineering-rules-people-and-agents-changing-this-project) (LF for Python/JS/JSON/MD/sh; CRLF ASCII or UTF-8 with BOM for `.ps1`).

## 2 Verification before committing

Run the repository checks and relevant unit tests before opening a pull request:

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

## 3 Privacy and redaction

Every commit must stay publishable (`tools/check-public.py` reports `0 findings`). Never commit:

- Personal names, email addresses, usernames, user profile paths (`C:\Users\<actual-name>\...`, `/home/<actual-name>/...`), or local tool directory names.
- Tailnet IPs (`100.x.y.z`), server hostnames/domains, SSH keys, executor tokens, or runtime files (`muselink.json`, `muselink.site.json`, `user-avatar.png`, `seen.json`, `pet-history.jsonl`).
- Unredacted production mailbox logs, screenshots containing personal data, or unconfirmed experience drafts.

## 4 Documentation and localization

- Documentation is maintained in English-default / Chinese pairs (`*.md` or `*.en.md` paired with `*.zh.md`), each starting with the language switch line (`English | [中文](CONTRIBUTING.zh.md)` and `[English](CONTRIBUTING.md) | 中文`).
- Both versions must keep the same number of level-two (`##`) headings and valid relative links/anchors (`python tools/check-docs.py`).
- All user-visible strings belong in `pc/i18n/{en,zh-Hans,zh-Hant}.json` and `vm/i18n/{en,zh-Hans,zh-Hant}.json` (`python tools/check-i18n.py`).

## 5 Experience library contributions

When sharing verified application methods to `library/entries/`:

- Locally confirmed entries are redacted and batched (5 verified entries per consent prompt; declining stops future prompts).
- Every shared entry must validate against `spec/experience.schema.json`, pass `pc/explib/redact.py` with zero sensitive hits, and fit the consent card preview.
- Do not modify `library/index.json` hashes by hand; submitted entries are dedicated under [CC0 1.0](library/LICENSE).

## 6 License and DCO

- Code and documentation are licensed under the [Apache License 2.0](LICENSE) (see [NOTICE](NOTICE)).
- Public experience entries under `library/` are dedicated under [CC0 1.0](library/LICENSE).
- All commits require a [Developer Certificate of Origin (DCO 1.1)](https://developercertificate.org/) sign-off (`git commit -s`).
