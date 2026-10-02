# Preparation pilot (experimental)

No real-provider pilot has been run; speed, cost and quality benefits are unmeasured. Clear briefs work with the effort pilot off. This is optional, off-by-default measurement: you run tasks in three ways (A, B, C cohorts), Rig records outcomes, and you decide if results warrant changing defaults. Rig never changes settings on its own. See [preparation-aware effort](smart-routing.md#preparation-aware-effort-opt-in-pilot).

## Three cohorts

| Cohort | How | Routing | Purpose |
|--------|-----|---------|---------|
| A | Hand-written brief | pilot **off** | Baseline (no preparation) |
| B | `rig_task_prepare` + pick | pilot **off** | Prep structure effect only |
| C | `rig_task_prepare` + pick | pilot **on** (`[routing] preparation_aware_effort = true` in `.rig/harness.toml`) | Effort adjustment effect (C - B) |

Run each task once per cohort on equivalent commits. The flag lives in `.rig/harness.toml` and is off by default. Exact model `supported_efforts` needed in `.rig/routing.json` for your model; built-in profiles currently singleton so flag alone cannot lower unsupported effort. Pick after toggling the flag (B↔C); Rig records which cohort each run belongs to.

## Run a task

1. Pick a real task (implement, mini, bulk; low-to-medium risk).
2. Note `parent_started_at` (UTC timestamp) before you start reading the repo.
3. Run your normal flow:
   - **Cohort A**: hand-written brief → pick → launch
   - **Cohort B/C**: `rig_task_prepare` → pick → launch
4. Same acceptance contract in all cohorts. Quality is the judge.
5. (Optional) Record parent token usage if your host reports it (`input`, `output`, `total`).

## Record and evaluate

Save a pilot JSON file (replace placeholders with your observed job IDs/timestamps):

```json
{
  "schema_version": 1,
  "pilot_id": "my-prep-pilot",
  "min_pairs": 10,
  "runs": [
    {"task_id": "fix-save", "cohort": "A", "job_id": "replace-with-job-a", "parent_started_at": "2026-10-02T09:00:00Z"},
    {"task_id": "fix-save", "cohort": "B", "job_id": "replace-with-job-b", "parent_started_at": "2026-10-02T10:00:00Z"},
    {"task_id": "fix-save", "cohort": "C", "job_id": "replace-with-job-c", "parent_started_at": "2026-10-02T11:00:00Z"}
  ]
}
```

Evaluate (read-only):

```bash
rig pilot evaluate --file pilot.json --json
```

Evaluation checks recorded cohort evidence; missing or pre-policy-v3 records do not count.

## Results

Per run: outcome (`accepted`, `failed`, `pending`, `unknown`), timing (parent start to first acceptance, pre-admission, worker execution, latency), acceptance (`first-pass` known/unknown), observed/parent-reported tokens.

Comparisons (B vs A, C vs B, C vs A) pair runs by `task_id`. A pair is comparable only when both runs were accepted and first-pass is known.

- **`quality-regression`**: a baseline-accepted task failed in the candidate, or a known first-pass regression, at any sample size and regardless of acceptance; no deltas are shown
- **`insufficient-evidence`**: fewer than `min_pairs` (default 10) comparable pairs; no deltas
- **`quality-held`**: otherwise; observed median paired deltas only (timing, tokens)

Pending/unknown never count as failures; missing metrics unknown; parent start after admission invalidates total/pre-admission. Tokens only exact fields (`input`, `output`, `reasoning`, `cached_input`, `total`), no estimates. Synthetic evidence labelled `synthetic-fixture`, not real quality proof. Small samples ≠ reason to change defaults.

`rig routing report` adds prep cohort status (present/absent/unknown) and pilot setting (on/off/n/a).
