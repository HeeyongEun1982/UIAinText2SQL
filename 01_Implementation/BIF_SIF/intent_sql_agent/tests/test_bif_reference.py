from __future__ import annotations

import unittest

from intent_sql_agent.services.bif_reference import normalize_bif_semantic_references


class BIFReferenceTests(unittest.TestCase):
    def test_repairs_invalid_one_based_pointer_for_only_metric(self) -> None:
        bif = {
            "metrics": [{"metric_id": "1", "name": "zip_code"}],
            "output_expectation": {
                "fields": [{"field_id": "1", "label": "Zip Code", "semantic_ref": "/metrics/1"}]
            },
        }
        normalized, repairs = normalize_bif_semantic_references(bif)
        self.assertEqual(
            "/metrics/0",
            normalized["output_expectation"]["fields"][0]["semantic_ref"],
        )
        self.assertEqual(1, len(repairs))
        self.assertEqual("/metrics/1", bif["output_expectation"]["fields"][0]["semantic_ref"])

    def test_does_not_guess_between_multiple_metrics(self) -> None:
        bif = {
            "metrics": [{"name": "amount"}, {"name": "count"}],
            "output_expectation": {
                "fields": [{"label": "unknown", "semantic_ref": "/metrics/2"}]
            },
        }
        normalized, repairs = normalize_bif_semantic_references(bif)
        self.assertEqual("/metrics/2", normalized["output_expectation"]["fields"][0]["semantic_ref"])
        self.assertEqual([], repairs)


if __name__ == "__main__":
    unittest.main()
