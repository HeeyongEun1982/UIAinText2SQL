from __future__ import annotations

import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src" / "intent_sql_agent"


class OrchestratorOwnershipTests(unittest.TestCase):
    def test_legacy_model_control_paths_do_not_exist_in_production(self) -> None:
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SOURCE.rglob("*.py")
        )
        forbidden = (
            "def a2_bif_gap",
            "def a5_sif_gap",
            "def a3_bif_patch",
            "def a6_sif_patch",
            "def apply_bif_response",
            "register_gap_report",
            "route_bif_gap_analysis",
            "normalize_sif_gap_analysis",
            "normalize_bif_gap",
            "apply_bif_change_set",
            "CLARIFICATION_SCHEMA",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, production)

    def test_only_orchestrator_defines_clarification_termination(self) -> None:
        definitions = []
        for path in SOURCE.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "def can_terminate(" in text or "def record_final_confirmation(" in text:
                definitions.append(path.name)
        self.assertEqual(["clarification_orchestrator.py"], sorted(set(definitions)))

    def test_removed_legacy_services_are_absent(self) -> None:
        services = SOURCE / "services"
        for filename in (
            "bif_resolution.py",
            "gap_router.py",
            "sif_gap_normalization.py",
        ):
            self.assertFalse((services / filename).exists(), filename)


if __name__ == "__main__":
    unittest.main()
