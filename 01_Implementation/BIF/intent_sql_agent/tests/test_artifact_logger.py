import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.services import ArtifactLogger


class ArtifactLoggerStepLayoutTest(unittest.TestCase):
    def test_artifacts_are_scoped_to_active_step_and_control_files_stay_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = ArtifactLogger(Path(temporary), "run-1", "q-1")
            logger.set_step("P02")
            logger.write_json("frames/bif.json", {"status": "draft"})
            logger.append_event("BIF_DRAFTED", {})
            logger.write_json("run_complete.json", {"status": "success"})
            logger.build_hash_manifest()

            self.assertTrue((logger.case_dir / "P02/frames/bif.json").is_file())
            self.assertFalse((logger.case_dir / "frames/bif.json").exists())
            self.assertTrue((logger.case_dir / "events.jsonl").is_file())
            self.assertTrue((logger.case_dir / "manifest.json").is_file())
            self.assertTrue((logger.case_dir / "run_complete.json").is_file())
            event = json.loads(
                (logger.case_dir / "events.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual("P02", event["payload"]["step"])

    def test_invalid_step_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = ArtifactLogger(Path(temporary), "run-1", "q-1")
            with self.assertRaises(ValueError):
                logger.set_step("P15")


if __name__ == "__main__":
    unittest.main()
