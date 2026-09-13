# Tmux bootstrap — 2026-09-14

First installation and `rig update` now attempt to provide tmux 3.3+ through existing Homebrew, apt-get, or dnf. Compatible tmux is skipped. Package operations use noninteractive stdin and bounded waits; dependency failure prints manual instructions and lets Rig installation continue. `RIG_SKIP_TMUX_INSTALL=1` supports managed environments. Companion activation remains explicit.

Review corrected dnf upgrade selection for older tmux and process-group cleanup when package-manager children outlive their parent. Tests mock package processes and version probes; no real package manager ran.

Validation: 55 focused tests passed: bootstrap 7, CLI 36, update 9, normal prompt flow 3. The normal prompt worker file-change check failed within the sandbox and passed on the authorized unsandboxed rerun. Real package downloads and installation were not exercised.

Docs impact: minor; README, usage, and flow explain bootstrap and opt-out. An unrelated `.gitignore` working-tree change was preserved because its provenance was uncertain.
