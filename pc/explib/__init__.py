"""explib - the open experience library (extension 5).

Design: docs/architecture.en.md section 10. Entries are files (source of
truth) under <tool dir>\\experience\\; nothing here drives the desktop: a
library hands out methods, the plugin's gated tools carry them out.

Layout under <tool dir>\\experience\\:
  local\\<app>\\<id>.json     this user's entries (never in the package)
  public\\<app>\\<id>.json    synced community entries, read-only
  outbox\\<id>.json           redacted entries the user agreed to share
  share.json                 consent state (section 3 of the design)
"""
__version__ = "0.1.0"
