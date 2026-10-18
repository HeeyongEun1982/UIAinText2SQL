from __future__ import annotations

import unittest

from intent_sql_agent.services.change_set import (
    apply_sif_system_change_set,
)
from intent_sql_agent.services.frame_state import frame_hash


class ChangeSetTests(unittest.TestCase):
    def test_sif_rejects_status_patch(self) -> None:
        frame = {"status": "needs_clarification", "query_blocks": []}
        updated, events = apply_sif_system_change_set(
            frame,
            {
                "base_frame_hash": frame_hash(frame),
                "operations": [{
                    "operation_type": "replace_unsupported_representation",
                    "path": "/status",
                    "value": "ready",
                }],
            },
        )
        self.assertEqual(frame, updated)
        self.assertIn(
            events[0]["reason"], {"operation_target_mismatch", "system_owned_path"}
        )

    def test_sif_typed_operation_cannot_target_another_node_kind(self) -> None:
        frame = {
            "status": "needs_grounding",
            "query_blocks": [{"sources": [], "limit": None}],
        }
        updated, events = apply_sif_system_change_set(
            frame,
            {
                "base_frame_hash": frame_hash(frame),
                "operations": [{
                    "operation_type": "replace_query_block",
                    "path": "/query_blocks/0/sources",
                    "value": [],
                }],
            },
        )
        self.assertEqual(frame, updated)
        self.assertEqual("operation_target_mismatch", events[0]["reason"])


if __name__ == "__main__":
    unittest.main()
