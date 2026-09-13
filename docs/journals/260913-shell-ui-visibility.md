# Shell UI visibility — 2026-09-13

Added opt-in bash/zsh integration for plain Codex/Grok terminal launches, using a session-local tmux status row and F8/F9 management popups. Host commands and arguments remain intact; worker/headless launches bypass the companion. Observations, queue receipts, and notices belong to the repository, independently of a parent turn.

Installation records original destination preimages before writes. Uninstall restores exact owned integrations and keeps edited host settings. Runtime removal is conditional on ownership, active usage, outstanding reservations, and remaining references. Project execution history, queue, and memory remain. Legacy installs can require manual cleanup.

Review found and corrected a macOS Unix-socket path overflow, duplicate observer startup mutating live ownership/receipts, and session pruning that could stall under continuous status traffic. Socket storage now uses a short private temporary path; observer lifetime has an exclusive lock and independent housekeeping. Lifecycle lock acquisition has a five-second deadline.

Validation: focused shell/install tests cover dependency failure without RC writes, symlink preservation, bash/zsh startup routing, original preimages across updates, active leases, registered projects, and legacy blocks. A real temporary observer process passed ping, snapshot, enqueue receipt, and duplicate-start checks. Final full suite: 638 tests passed in 113.710 seconds. The latest ownership regression suite also passed all eight tests after retaining registered-repository knowledge across repeat uninstall. Real terminal acceptance covered Grok and Codex screen modes, F8 popup/Esc restoration, and a busy test host accepting one Unicode F9 queue submission. Detailed results are recorded in the implementation reports; no user-home installation was performed for these tests.

Limitations: preserving an edited host config can leave runtime references, so uninstall may be partial. Native cancellation still requires the owning host when Rig cannot confirm interruption. A GUI-hosted parent does not gain this terminal companion.

A single warm terminal fixture measured popup first frame at 50.76ms, durable enqueue receipt at 6.78ms, and observer queue visibility at 6.92ms. These measurements do not establish cold-start, loaded, or live model-turn performance.
