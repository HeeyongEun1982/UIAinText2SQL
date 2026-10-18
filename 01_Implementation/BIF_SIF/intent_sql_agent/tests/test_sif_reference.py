from __future__ import annotations

import unittest

from intent_sql_agent.services.sif_reference import normalize_sif_intent_references


class SIFReferenceTests(unittest.TestCase):
    def test_repairs_only_invalid_one_based_condition_pointer(self) -> None:
        bif = {"metrics": [{}], "target_population": {"inclusion_conditions": [{}, {}, {}]}}
        sif = {"query_blocks": [{"where": [{
            "kind": "binary", "intent_paths": ["/target_population/inclusion_conditions/3"],
        }]}]}
        normalized, repairs = normalize_sif_intent_references(sif, bif)
        self.assertEqual(
            ["/target_population/inclusion_conditions/2"],
            normalized["query_blocks"][0]["where"][0]["intent_paths"],
        )
        self.assertEqual(1, len(repairs))


if __name__ == "__main__":
    unittest.main()
