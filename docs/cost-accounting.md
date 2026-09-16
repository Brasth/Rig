# Cost accounting

Rig records **observed tokens** and **actual invoiced USD** as separate facts. It never estimates cost from tokens, list prices, subscriptions, or token totals, and it never invents a missing field.

## Safety boundary

- Tokens are observed-only. Unknown stays unknown. Provenance is `parent`, `wrapper`, or `native_child` when available.
- Dollars come only from imported or synced **invoice receipts**. Amounts are exact `Decimal` strings; fractions beyond cents are kept.
- The ledger is provider-neutral and scoped. It stores **credential references** (`env`, `config_id`, `organization_id`, `project_id`) never secret values.
- Receipts are **cohort aggregates**. If a provider bills an account or period rather than a job, Rig will not allocate those dollars onto individual jobs.
- OpenAI and Anthropic adapters are read-only. Network is explicit (`--network`). Dry-run/validate writes nothing. Generic providers use receipt import.
- Parent-only mutations. Worker jobs cannot import receipts or record benchmark outcomes.
- Public CLI/MCP output is JSON-compatible and redacts credentials/tokens.

## Setup

Create `.rig/billing.json` with named scopes (accounts, projects, or studies). A missing file uses a `default` scope listing every known route provider (`openai`, `anthropic`, `xai`, `google`, `cursor`, `cognition`).

```json
{
  "schema_version": 1,
  "active_scope": "editor-study",
  "scopes": {
    "editor-study": {
      "id": "editor-study",
      "providers": ["openai", "anthropic"],
      "credential_refs": {
        "openai": {"env": "OPENAI_API_KEY", "config_id": "org-openai-demo"},
        "anthropic": {"env": "ANTHROPIC_API_KEY", "organization_id": "org-anthropic-demo"}
      }
    }
  }
}
```

`credential_refs` are identifiers only. Putting an API key, bearer token, or `sk-` value in this file is rejected.

## Receipt import and sync

Each stored receipt keeps: `provider`, `period`, exact `amount_usd`, `currency` (USD), `source_identity`, `cohort`, `source`, and retrieval/import `evidence`.

```bash
# Generic provider import (xAI, Google, Cursor, Cognition, or a manual OpenAI/Anthropic file)
rig billing import --file receipt.json --json
rig billing import --file receipt.json --dry-run --json

# First-class OpenAI / Anthropic adapters. Provider is positional; flags stay opt-in.
# Network never runs unless --network is set and a read-only fetch is injected.
# `--provider openai` is equivalent. Adapters never print credential values.
rig billing sync openai --file openai-invoices.json --json
rig billing sync anthropic --file anthropic-invoices.json --dry-run --json
rig billing sync openai --network --json

rig billing report --json
```

MCP: `rig_billing_import`, `rig_billing_sync` (`dry_run`, `network`), `rig_billing_report`.

Example receipt:

```json
{
  "receipt_id": "inv-2026-09",
  "provider": "openai",
  "amount_usd": "20.001",
  "currency": "USD",
  "period": {"start": "2026-09-01", "end": "2026-09-30"},
  "source_identity": "org-openai-demo",
  "cohort": "rig",
  "evidence": {"kind": "import", "method": "file"}
}
```

`job_id` is rejected. Cohort totals are the unit of dollar coverage.

## 20-task benchmark procedure

1. Freeze a paired spec under `.rig/benchmarks/<id>/spec.json` with **baseline** and **rig** arms and unique tasks. The `opus-openai-editor` baseline arm must be `prose-only`.
2. Run the same 20 tasks on both arms. Attribute only **currently accepted** jobs.
3. Import actual invoice receipts into the matching cohorts (`rig`, `baseline`) for the same period/scope.
4. Report. A savings **conclusion** requires **exactly 20 matched completed pairs**, non-inferior quality, and comparable covered actual dollars.

```bash
rig benchmark create --file spec.json --json
rig benchmark outcome --id opus-openai-editor --job JOB --task TASK --arm rig --json
rig benchmark report --id opus-openai-editor --json
```

MCP: `rig_benchmark_create`, `rig_benchmark_outcome`, `rig_benchmark_report`.

## Calculations

Reports keep tokens and dollars separate:

| Signal | Meaning |
| --- | --- |
| Token coverage | Count of jobs with observed structured usage vs unknown. Unknown is not zero. |
| Dollar coverage | Cohort invoice totals. Missing cohort receipts are reported as missing. |
| Quality gate | `incomplete` (fewer than 20 matched current pairs), `inferior` (Rig accepted fewer than baseline), or `non_inferior`. |
| Savings | Claimed only when quality is non-inferior, both cohorts have actual USD, there are 20 matched completed pairs, and Rig cohort dollars are lower. |

No token×price math, no list-price table, no seat amortization. `delta_usd` is exact `Decimal` subtraction of cohort totals.

## Limitations

- Wrapper token usage is persisted only from one unambiguous final structured event (`type=result`, or Grok `type=end` with `stopReason`). Totals are never derived.
- Parent usage stays unknown unless the parent supplies the canonical object on finish.
- Provider invoices are typically account/period totals. **Dollars cannot be assigned to individual jobs if the provider reports only cohort aggregates.**
- OpenAI/Anthropic adapters do not call the network unless `network=true` and a read-only fetch is injected. There is no built-in live HTTP client in the ledger.
- A 19-task sample cannot produce a savings conclusion. Stale or unaccepted outcomes drop out of the pair count.
- Routing reports (`rig_routing_report`) still ignore dollar data.

See [Rig flow](rig-flow.md) for ownership close, including the parent-only break-glass path. See [smart routing](smart-routing.md) for token coverage on routing attempts.
