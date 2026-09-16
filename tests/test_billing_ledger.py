#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import billing_ledger as ledger  # noqa: E402
import rig_mcp  # noqa: E402


def _receipt(**fields):
    base = {
        "receipt_id": "inv-1",
        "provider": "openai",
        "amount_usd": "1.20",
        "currency": "USD",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "source_identity": "openai-org-demo",
        "cohort": "default",
    }
    base.update(fields)
    return base


class BillingLedger(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / ".git").mkdir()
        (self.repo / ".rig").mkdir()
        os.environ.pop("RIG_JOB_ID", None)
        os.environ.pop("RIG_JOB_DIR", None)

    def tearDown(self):
        self.td.cleanup()

    def test_malformed_receipts_rejected(self):
        for bad in (
            {"receipt_id": "inv-1", "provider": "openai", "amount_usd": 1.2, "period": "2026-09"},
            {"receipt_id": "inv-1", "provider": "openai", "amount_usd": "-1.00", "period": "2026-09"},
            {"receipt_id": "inv-1", "provider": "openai", "amount_usd": "1e2", "period": "2026-09"},
            {"receipt_id": "inv-1", "provider": "acme", "amount_usd": "1.00", "period": "2026-09"},
            {"receipt_id": "inv 1", "provider": "openai", "amount_usd": "1.00", "period": "2026-09"},
            {"receipt_id": "inv-1", "provider": "openai", "amount_usd": "1.00", "currency": "EUR", "period": "2026-09"},
            {"receipt_id": "inv-1", "provider": "openai", "amount_usd": "1.00"},
        ):
            with self.assertRaises(ledger.LedgerError):
                ledger.normalize_receipt(bad, scope="default")

    def test_fractions_beyond_cents_are_preserved(self):
        got = ledger.normalize_usd("0.001")
        self.assertEqual(got, "0.001")
        self.assertEqual(Decimal(got), Decimal("0.001"))
        first = ledger.import_receipt(self.repo, _receipt(amount_usd="1.2345"))
        self.assertEqual(first["amount_usd"], "1.2345")
        second = ledger.import_receipt(self.repo, _receipt(
            receipt_id="inv-2", provider="anthropic", amount_usd="0.0007", cohort="baseline",
            source_identity="anthropic-org-demo",
        ))
        totals = ledger.provider_totals(ledger.load_receipts(self.repo))
        self.assertEqual(totals["all"], "1.2352")
        self.assertEqual(ledger.add_usd("1.2345", "0.0007"), "1.2352")
        self.assertNotEqual(ledger.normalize_usd("1.2345"), "1.23")
        self.assertEqual(second["amount_usd"], "0.0007")

    def test_no_inferred_dollars_or_job_allocation(self):
        with self.assertRaisesRegex(ledger.LedgerError, "inferred"):
            ledger.normalize_receipt(_receipt(total_cost_usd="9.99"), scope="default")
        with self.assertRaisesRegex(ledger.LedgerError, "individual jobs"):
            ledger.normalize_receipt(_receipt(job_id="job-a"), scope="default")
        with self.assertRaisesRegex(ledger.LedgerError, "individual jobs"):
            ledger.job_dollar_allocation(self.repo, "job-a")
        with self.assertRaisesRegex(ledger.LedgerError, "inferred"):
            ledger.import_receipt(self.repo, _receipt(list_price="3.00"))

    def test_idempotent_import_and_conflict(self):
        first = ledger.import_receipt(self.repo, _receipt())
        self.assertTrue(first["created"])
        self.assertEqual(first["amount_usd"], "1.20")
        self.assertEqual(first["period"]["start"], "2026-09-01")
        self.assertTrue(first["source_identity"])
        self.assertIn("evidence", first)
        again = ledger.import_receipt(self.repo, _receipt())
        self.assertFalse(again["created"])
        with self.assertRaises(ledger.LedgerError):
            ledger.import_receipt(self.repo, _receipt(amount_usd="2.00"))
        self.assertEqual(len(ledger.load_receipts(self.repo)), 1)

    def test_generic_provider_import_and_dry_run(self):
        dry = ledger.import_receipt(self.repo, _receipt(provider="xai", amount_usd="4.50"),
                                    source="generic_import", dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertFalse((self.repo / ".rig" / "billing" / "default" / "receipts" / "inv-1.json").is_file())
        stored = ledger.import_receipt(self.repo, _receipt(provider="xai", amount_usd="4.50"),
                                       source="generic_import")
        self.assertTrue(stored["created"])
        self.assertEqual(stored["source"], "generic_import")
        self.assertEqual(stored["provider"], "xai")

    def test_no_secrets_in_config_or_receipts(self):
        (self.repo / ".rig" / "billing.json").write_text(json.dumps({
            "schema_version": 1, "active_scope": "default",
            "scopes": {"default": {"providers": ["openai"], "api_key": "sk-secret"}},
        }))
        with self.assertRaisesRegex(ledger.LedgerError, "credentials"):
            ledger.load_config(self.repo)
        (self.repo / ".rig" / "billing.json").unlink()
        with self.assertRaisesRegex(ledger.LedgerError, "credentials"):
            ledger.import_receipt(self.repo, _receipt(api_key="sk-live"))
        with self.assertRaisesRegex(ledger.LedgerError, "credentials"):
            ledger.normalize_receipt(_receipt(token="secret"), scope="default")
        (self.repo / ".rig" / "billing.json").write_text(json.dumps({
            "schema_version": 1, "active_scope": "default",
            "scopes": {"default": {
                "providers": ["openai"],
                "credential_refs": {"openai": {"env": "OPENAI_API_KEY", "config_id": "org-demo"}},
            }},
        }))
        cfg = ledger.load_config(self.repo)
        self.assertEqual(cfg["scopes"]["default"]["credential_refs"]["openai"]["env"], "OPENAI_API_KEY")
        self.assertNotIn("sk-", json.dumps(cfg))

    def test_credential_refs_reject_secret_values(self):
        (self.repo / ".rig" / "billing.json").write_text(json.dumps({
            "schema_version": 1, "active_scope": "default",
            "scopes": {"default": {
                "providers": ["openai"],
                "credential_refs": {"openai": {"env": "sk-live-secret"}},
            }},
        }))
        with self.assertRaisesRegex(ledger.LedgerError, "credentials"):
            ledger.load_config(self.repo)

    def test_cohort_totals_not_per_job(self):
        ledger.import_receipt(self.repo, _receipt(cohort="rig"))
        ledger.import_receipt(self.repo, _receipt(
            receipt_id="inv-2", provider="anthropic", amount_usd="3.05",
            cohort="baseline", source_identity="anthropic-org-demo",
        ))
        totals = ledger.provider_totals(ledger.load_receipts(self.repo))
        self.assertEqual(totals["openai"], "1.20")
        self.assertEqual(totals["anthropic"], "3.05")
        self.assertEqual(totals["all"], "4.25")
        cohorts = ledger.cohort_totals(ledger.load_receipts(self.repo))
        self.assertEqual(cohorts["rig"], "1.20")
        self.assertEqual(cohorts["baseline"], "3.05")
        report = ledger.build_report(self.repo)
        self.assertEqual(report["job_attribution"], "unavailable")
        self.assertIn("individual jobs", report["note"])

    def test_sync_adapters_are_mockable_and_never_network(self):
        with self.assertRaisesRegex(ledger.LedgerError, "no automatic network"):
            ledger.sync_receipts(self.repo, "openai")
        rows = [
            _receipt(receipt_id="oa-1", cohort="rig"),
            _receipt(receipt_id="oa-2", amount_usd="0.40", cohort="rig"),
        ]
        adapter = ledger.openai_adapter(fetch=lambda: rows, credential_ref={"env": "OPENAI_API_KEY"})
        plan = adapter.plan(network=False, dry_run=True)
        self.assertEqual(plan["provider"], "openai")
        self.assertIn("api.openai.com", plan["endpoint"])
        self.assertEqual(plan["credential_ref"]["env"], "OPENAI_API_KEY")
        self.assertNotIn("sk-", json.dumps(plan))
        self.assertFalse(plan["network"])
        result = ledger.sync_receipts(self.repo, "openai", adapter=adapter)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["receipts"][0]["source"], "openai_sync")
        self.assertIn("individual jobs", result["note"])
        anthropic = ledger.anthropic_adapter(fetch=lambda: [
            _receipt(receipt_id="an-1", provider="anthropic", amount_usd="8.00", cohort="baseline",
                     source_identity="anthropic-org-demo"),
        ])
        synced = ledger.sync_receipts(self.repo, "anthropic", adapter=anthropic)
        self.assertEqual(synced["receipts"][0]["source"], "anthropic_sync")
        self.assertIn("api.anthropic.com", anthropic.plan()["endpoint"])
        text = json.dumps(ledger.load_receipts(self.repo))
        self.assertNotIn("sk-", text)
        self.assertNotIn("api_key", text)
        dry = ledger.sync_receipts(
            self.repo, "openai", receipts=[_receipt(receipt_id="dry-1")], dry_run=True,
        )
        self.assertTrue(dry["dry_run"])
        self.assertFalse((self.repo / ".rig" / "billing" / "default" / "receipts" / "dry-1.json").is_file())

    def test_adapter_validation_rejects_network_without_fetch(self):
        adapter = ledger.openai_adapter()
        with self.assertRaisesRegex(ledger.LedgerError, "no automatic network"):
            adapter.receipts()
        with self.assertRaisesRegex(ledger.LedgerError, "injected read-only fetch"):
            adapter.receipts(network=True)
        with self.assertRaisesRegex(ledger.LedgerError, "no automatic network"):
            ledger.sync_receipts(self.repo, "openai", network=True)

    def test_mcp_import_report_and_routing_report_compatible(self):
        imported = rig_mcp.call_tool("rig_billing_import", {
            "repo": str(self.repo), "receipt": _receipt(amount_usd="0.50"),
        })
        self.assertNotIn("isError", imported, imported)
        self.assertNotIn("sk-", imported["content"][0]["text"])
        report = rig_mcp.call_tool("rig_billing_report", {"repo": str(self.repo)})
        self.assertIn("usd=0.50", report["content"][0]["text"])
        self.assertEqual(report["structuredContent"]["job_attribution"], "unavailable")
        routing = rig_mcp.call_tool("rig_routing_report", {"repo": str(self.repo)})
        self.assertIn("structuredContent", routing)
        self.assertEqual(routing["structuredContent"]["days"], 30)
        dry = rig_mcp.call_tool("rig_billing_sync", {
            "repo": str(self.repo), "provider": "openai",
            "receipts": [_receipt(receipt_id="mcp-oa", amount_usd="0.125")],
            "dry_run": True,
        })
        self.assertNotIn("isError", dry, dry)
        self.assertTrue(dry["structuredContent"]["dry_run"])
        self.assertEqual(dry["structuredContent"]["receipts"][0]["amount_usd"], "0.125")
        leaked = json.dumps(dry)
        self.assertNotIn("owner_token", leaked)
        self.assertNotIn("sk-", leaked)

    def test_adapter_aliases_strip_unknown_keys_and_keep_decimal(self):
        payload = {
            "receipt_id": "oa-alias",
            "provider": "openai",
            "amount": {"value": "1.2345", "currency": "usd"},
            "start": "2026-09-01",
            "end": "2026-09-30",
            "object": "invoice",
            "created": 123456,
            "organization_id": "org-demo",
            "source_identity": "openai-org-demo",
            "cohort": "rig",
        }
        rows = ledger._coerce_receipt_rows(payload, provider="openai")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount_usd"], "1.2345")
        self.assertEqual(rows[0]["period"], {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertNotIn("object", rows[0])
        self.assertNotIn("created", rows[0])
        self.assertNotIn("amount", rows[0])
        self.assertNotIn("start", rows[0])
        self.assertNotIn("organization_id", rows[0])
        adapter = ledger.openai_adapter(fetch=lambda: payload)
        parsed = adapter.parse(payload)
        self.assertEqual(parsed[0]["amount_usd"], "1.2345")
        self.assertEqual(Decimal(parsed[0]["amount_usd"]), Decimal("1.2345"))
        result = ledger.sync_receipts(self.repo, "openai", receipts=[payload])
        self.assertEqual(result["receipts"][0]["amount_usd"], "1.2345")
        stored = ledger.load_receipts(self.repo)[0]
        self.assertEqual(stored["amount_usd"], "1.2345")
        self.assertNotIn("object", stored)
        string_amount = dict(payload)
        string_amount["receipt_id"] = "oa-str"
        string_amount["amount"] = "0.001"
        string_amount.pop("amount_usd", None)
        again = ledger.sync_receipts(self.repo, "openai", receipts=[string_amount])
        self.assertEqual(again["receipts"][0]["amount_usd"], "0.001")
        with self.assertRaisesRegex(ledger.LedgerError, "individual jobs"):
            ledger._coerce_receipt_rows({**payload, "receipt_id": "oa-job", "job_id": "job-a"}, provider="openai")

    def test_cli_sync_positional_provider_and_safe_flags(self):
        path = self.repo / "invoices.json"
        path.write_text(json.dumps([_receipt(receipt_id="cli-oa", amount_usd="0.125")]))
        env = {
            key: value for key, value in os.environ.items()
            if key not in {
                "RIG_JOB_ID", "RIG_JOB_DIR", "RIG_OWNER_TOKEN",
                "RIG_RESERVATION_ID", "RIG_ATTEMPT_ID",
            }
        }
        script = str(ROOT / "scripts" / "billing_ledger.py")
        positional = self._run_ledger(
            ["--repo", str(self.repo), "sync", "openai", "--file", str(path), "--json"], env,
        )
        self.assertEqual(positional.returncode, 0, positional.stderr)
        body = json.loads(positional.stdout)
        self.assertEqual(body["provider"], "openai")
        self.assertEqual(body["receipts"][0]["amount_usd"], "0.125")
        self.assertNotIn("sk-", positional.stdout)
        self.assertNotIn("api_key", positional.stdout)
        self.assertNotIn("owner_token", positional.stdout)
        dry_path = self.repo / "dry.json"
        dry_path.write_text(json.dumps([_receipt(
            receipt_id="cli-dry", provider="anthropic", amount_usd="9.00",
            source_identity="anthropic-org-demo",
        )]))
        dry = self._run_ledger(
            ["--repo", str(self.repo), "sync", "anthropic", "--file", str(dry_path),
             "--dry-run", "--json"], env,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        dry_body = json.loads(dry.stdout)
        self.assertTrue(dry_body["dry_run"])
        self.assertFalse((self.repo / ".rig" / "billing" / "default" / "receipts" / "cli-dry.json").is_file())
        flag_path = self.repo / "flag.json"
        flag_path.write_text(json.dumps([_receipt(receipt_id="cli-flag", amount_usd="0.50")]))
        flagged = self._run_ledger(
            ["--repo", str(self.repo), "sync", "--provider", "openai",
             "--file", str(flag_path), "--validate", "--json"], env,
        )
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        self.assertTrue(json.loads(flagged.stdout)["dry_run"])
        self.assertFalse((self.repo / ".rig" / "billing" / "default" / "receipts" / "cli-flag.json").is_file())
        missing = self._run_ledger(
            ["--repo", str(self.repo), "sync", "--json"], env,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("openai", missing.stderr)
        network = self._run_ledger(
            ["--repo", str(self.repo), "sync", "openai", "--network", "--json"], env,
        )
        self.assertEqual(network.returncode, 2)
        self.assertIn("no automatic network", network.stderr)
        self.assertNotIn("sk-", network.stderr)

    def _run_ledger(self, argv, env):
        import subprocess
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "billing_ledger.py"), *argv],
            capture_output=True, text=True, env=env,
        )

    def test_mcp_rejects_secret_receipts_without_leaking(self):
        denied = rig_mcp.call_tool("rig_billing_import", {
            "repo": str(self.repo), "receipt": _receipt(api_key="sk-live-secret"),
        })
        self.assertTrue(denied.get("isError"))
        self.assertNotIn("sk-live-secret", denied["content"][0]["text"])
        self.assertNotIn("sk-", denied["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
