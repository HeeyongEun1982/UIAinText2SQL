"""Small-N descriptive checks for database schema size and RQ3 outcomes.

The database is the unit (N=11).  These correlations are exploratory boundary
descriptions, not confirmatory tests and not evidence of a causal schema-size
effect.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr


HERE = Path(__file__).resolve().parent
INPUT = HERE / "outputs" / "boundary_schema_size_by_database.csv"
OUTPUT = HERE / "outputs" / "boundary_schema_size_correlations.csv"
MANIFEST = HERE / "outputs" / "boundary_schema_size_correlations.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> None:
    data = pd.read_csv(INPUT)
    if len(data) != 11 or data["group"].nunique() != 11:
        raise ValueError("Expected one row for each of the 11 databases")
    data = data.assign(
        combined_minus_bif=data["bif_sif_ex"] - data["bif_only_ex"],
        combined_minus_sif=data["bif_sif_ex"] - data["sif_only_ex"],
    )
    size_fields = ["schema_table_count", "schema_column_count"]
    outcome_fields = [
        "bif_sif_ex",
        "combined_minus_bif",
        "combined_minus_sif",
        "combined_gain_any_rate",
        "combined_regression_any_rate",
    ]
    rows = []
    for size in size_fields:
        for outcome in outcome_fields:
            result = spearmanr(data[size], data[outcome])
            rows.append(
                {
                    "unit": "database",
                    "n_databases": len(data),
                    "schema_size_measure": size,
                    "outcome_measure": outcome,
                    "spearman_rho": float(result.statistic),
                    "two_sided_p": float(result.pvalue),
                    "analysis_role": "exploratory descriptive boundary check",
                }
            )
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "scope": "Exploratory database-level (N=11) correlations; no causal or confirmatory interpretation.",
        "input_sha256": sha256(INPUT),
        "script_sha256": sha256(Path(__file__).resolve()),
        "output_sha256": sha256(OUTPUT),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
