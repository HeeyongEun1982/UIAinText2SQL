from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArtifactLogger:
    def __init__(self, run_root: Path, run_id: str, question_id: str) -> None:
        self.case_dir = (run_root / run_id / question_id).resolve()
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.case_dir / "events.jsonl"
        self.active_step: str | None = None

    def set_step(self, step: str) -> None:
        if not re.fullmatch(r"P(?:0[0-9]|1[0-4])", step):
            raise ValueError(f"Invalid pipeline step: {step}")
        self.active_step = step

    def write_json(self, relative_path: str, value: Any) -> Path:
        path = self._artifact_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path

    def write_text(self, relative_path: str, value: str) -> Path:
        path = self._artifact_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.active_step and "step" not in payload:
            payload = {"step": self.active_step, **payload}
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def build_hash_manifest(self) -> dict[str, str]:
        manifest: dict[str, str] = {}
        for path in sorted(self.case_dir.rglob("*")):
            if path.is_file() and path.name not in {"manifest.json", "run_complete.json"}:
                relative = path.relative_to(self.case_dir).as_posix()
                manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_root_json("manifest.json", {"sha256": manifest})
        return manifest

    def verify_hash_manifest(self) -> bool:
        manifest_path = self.case_dir / "manifest.json"
        if not manifest_path.is_file():
            return False
        expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("sha256", {})
        for relative, digest in expected.items():
            path = self._safe_path(relative)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return False
        return True

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.case_dir / relative_path).resolve()
        if self.case_dir != candidate and self.case_dir not in candidate.parents:
            raise ValueError("Artifact path escapes the case directory")
        return candidate

    def write_root_json(self, relative_path: str, value: Any) -> Path:
        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path

    def _artifact_path(self, relative_path: str) -> Path:
        if relative_path in {"manifest.json", "run_complete.json"}:
            return self._safe_path(relative_path)
        if self.active_step:
            return self._safe_path(f"{self.active_step}/{relative_path}")
        return self._safe_path(relative_path)
