#!/usr/bin/env python3
"""Receipt normalization, PNG hash, and image fallback. No Cua Driver."""
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import cu_receipt

# 1x1 PNG fixture (not a live screenshot).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _evidence(**overrides):
    data = {
        "ok": True,
        "effective": True,
        "action": "capture",
        "snapshot_id": "drv-1",
        "pid": 8,
        "window_id": 12,
        "addressed": {
            "kind": "ax",
            "element_token": "tok-1",
            "index": 1,
            "label": "1",
            "ref": "",
            "x": None,
            "y": None,
        },
        "effect": "captured",
        "before_png": "",
        "after_png": "",
        "elements": [{"index": 1, "role": "AXButton", "label": "1", "element_token": "tok-1"}],
        "outline": "Calculator",
        "coord_space": "window_local",
        "brief_block": "Child must not click.\n",
        "hint": "act only with a token from this snapshot",
    }
    data.update(overrides)
    return data


class CuReceipt(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.png = Path(self.td.name) / "shot.png"
        self.png.write_bytes(PNG_1X1)

    def test_normalize_versioned_receipt_and_hash(self):
        digest = hashlib.sha256(PNG_1X1).hexdigest()
        receipt = cu_receipt.normalize(_evidence(after_png=str(self.png)))
        self.assertEqual(receipt["version"], "rig.cu.v1")
        self.assertEqual(receipt["operation"], "capture")
        self.assertEqual(receipt["status"], "captured")
        self.assertEqual(receipt["snapshot"]["id"], "drv-1")
        self.assertEqual(receipt["snapshot"]["freshness"], "fresh")
        self.assertEqual(receipt["snapshot"]["coord_space"], "window_local")
        self.assertEqual(receipt["observation"]["outline"], "Calculator")
        self.assertEqual(receipt["observation"]["elements"][0]["element_token"], "tok-1")
        self.assertEqual(receipt["target"]["element_token"], "tok-1")
        self.assertEqual(receipt["effect"], "captured")
        self.assertEqual(receipt["next_action"], "act")
        self.assertTrue(receipt["image"]["available"])
        self.assertEqual(receipt["image"]["mime"], "image/png")
        self.assertEqual(receipt["image"]["sha256"], digest)
        self.assertIn("Child must not click", receipt["brief_block"])

    def test_missing_png_keeps_valid_unavailable_receipt(self):
        receipt = cu_receipt.normalize(_evidence(after_png="/no/such/shot.png"))
        self.assertEqual(receipt["version"], "rig.cu.v1")
        self.assertEqual(receipt["snapshot"]["id"], "drv-1")
        self.assertFalse(receipt["image"]["available"])
        self.assertEqual(receipt["image"]["mime"], "")
        self.assertEqual(receipt["image"]["sha256"], "")
        self.assertIsNone(cu_receipt.image_content(_evidence(after_png="/no/such/shot.png")))

    def test_unreadable_or_non_png_is_unavailable(self):
        empty = Path(self.td.name) / "empty.png"
        empty.write_bytes(b"")
        junk = Path(self.td.name) / "junk.png"
        junk.write_bytes(b"not a png")
        folder = Path(self.td.name) / "dir.png"
        folder.mkdir()
        for path in (empty, junk, folder):
            receipt = cu_receipt.normalize(_evidence(after_png=str(path)))
            self.assertFalse(receipt["image"]["available"], path)
            self.assertEqual(receipt["image"]["sha256"], "")
            self.assertIsNone(cu_receipt.image_content(_evidence(after_png=str(path))))

    def test_stale_maps_to_capture_required(self):
        receipt = cu_receipt.normalize(_evidence(
            ok=False, action="click", effect="stale",
            snapshot_freshness="expired", hint="capture_required: snapshot expired",
        ))
        self.assertEqual(receipt["status"], "capture_required")
        self.assertEqual(receipt["effect"], "stale")
        self.assertEqual(receipt["next_action"], "capture")
        self.assertEqual(receipt["snapshot"]["freshness"], "expired")

    def test_act_success_next_is_confirm(self):
        receipt = cu_receipt.normalize(_evidence(
            action="click", effect="unverifiable", snapshot_freshness="confirm_required",
        ))
        self.assertEqual(receipt["status"], "unverifiable")
        self.assertEqual(receipt["next_action"], "confirm")
        self.assertEqual(receipt["snapshot"]["freshness"], "confirm_required")

    def test_image_content_when_png_readable(self):
        image = cu_receipt.image_content(_evidence(after_png=str(self.png)))
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]), PNG_1X1)

    def test_prefers_after_png_over_before(self):
        before = Path(self.td.name) / "before.png"
        before.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        receipt = cu_receipt.normalize(_evidence(
            before_png=str(before), after_png=str(self.png),
        ))
        self.assertEqual(receipt["image"]["sha256"], hashlib.sha256(PNG_1X1).hexdigest())

    def test_present_mcp_includes_image_and_legacy_keys(self):
        out = cu_receipt.present_mcp(_evidence(after_png=str(self.png)))
        self.assertEqual(out["content"][0]["type"], "text")
        self.assertIn("Child must not click", out["content"][0]["text"])
        self.assertEqual(out["content"][1]["type"], "image")
        self.assertEqual(out["structuredContent"]["snapshot_id"], "drv-1")
        self.assertEqual(out["structuredContent"]["receipt"]["version"], "rig.cu.v1")
        self.assertTrue(out["structuredContent"]["receipt"]["image"]["available"])

    def test_present_mcp_omits_image_when_unavailable(self):
        out = cu_receipt.present_mcp(_evidence(after_png=""))
        types = [item["type"] for item in out["content"]]
        self.assertEqual(types, ["text"])
        self.assertFalse(out["structuredContent"]["receipt"]["image"]["available"])
        self.assertIn("Child must not click", out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
