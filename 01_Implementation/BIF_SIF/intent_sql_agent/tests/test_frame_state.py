from __future__ import annotations

import unittest

from intent_sql_agent.services.frame_state import create_frame_envelope, frame_hash


class FrameStateTests(unittest.TestCase):
    def test_envelope_contains_no_incremental_patch_state(self) -> None:
        frame = {"readiness": {"state": "draft"}, "scope": {}}
        envelope = create_frame_envelope(frame, layer="bif")
        self.assertEqual("draft", envelope["state"])
        self.assertEqual(frame_hash(frame), envelope["frame_hash"])
        self.assertTrue(envelope["frame_id"].startswith("BIF-"))
        self.assertEqual(1, envelope["revision"])
        self.assertNotIn("applied_patches", envelope)
        self.assertNotIn("rejected_patches", envelope)


if __name__ == "__main__":
    unittest.main()
