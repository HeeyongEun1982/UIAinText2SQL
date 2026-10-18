from __future__ import annotations
import hashlib
import json
from copy import deepcopy

class CQDFreezer:
    def freeze(self, cqd: dict, rendered_text: str) -> dict:
        out = deepcopy(cqd)
        out["status"] = "confirmed"
        out["rendered_text"] = rendered_text
        canonical = deepcopy(out)
        canonical["traceability"] = {k: v for k, v in canonical.get("traceability", {}).items() if k != "state_hash"}
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        out.setdefault("traceability", {})["state_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        out["cqd_id"] = out["cqd_id"].replace("-initial", "-confirmed")
        return out
