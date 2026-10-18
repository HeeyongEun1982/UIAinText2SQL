from __future__ import annotations

import unittest

from intent_sql_agent.services.cqd_sql_direct_compiler import (
    compile_cqd_directly_to_sql,
)


def _feedback(issue: dict, label: str) -> dict:
    return {
        "type": "TECHNICAL_CLARIFICATION_RESPONSE",
        "surface": issue["surface"],
        "candidates": issue["candidates"],
        "response": {
            "selected_option_ids": [f"option_{issue['candidates'].index(label) + 1}"],
        },
    }


class DirectCQDSQLCompilerTests(unittest.TestCase):
    def test_q2_links_cqd_phrases_directly_and_inserts_fk_join(self) -> None:
        cqd = {
            "populations": [{"entity": "charter schools"}],
            "predicates": {"atoms": [
                {"dimension": "Charter School (Y/N)", "operator": "=", "value": 1,
                 "source_text": "charter schools"},
                {"dimension": "County Office of Education", "operator": "=",
                 "value": "Fresno", "source_text": "in Fresno County Office of Education"},
            ]},
            "outputs": [{"attribute": "zip code"}],
        }
        catalog = {"tables": [
            {"name": "frpm", "columns": [
                {"name": "CDSCode"}, {"name": "Charter School (Y/N)"},
                {"name": "District Name"},
            ], "question_value_matches": [
                {"column": "District Name", "value": "Fresno County Office of Education"},
            ], "foreign_keys": []},
            {"name": "schools", "columns": [
                {"name": "CDSCode"}, {"name": "Zip", "description": "zip code"},
                {"name": "County"},
            ], "question_value_matches": [
                {"column": "County", "value": "Fresno"},
            ], "foreign_keys": [{
                "target_table": "frpm", "from_column": "CDSCode", "to_column": "CDSCode",
            }]},
        ]}
        first = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=None,
        )
        issue = next(
            item for item in first.issues
            if any("District Name" in candidate for candidate in item["candidates"])
        )
        final = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=None,
            technical_feedback=[_feedback(issue, "frpm.District Name")],
        )
        self.assertEqual(final.issues, [])
        self.assertIn('INNER JOIN "frpm"', final.sql)
        self.assertIn('"frpm"."District Name" = \'Fresno County Office of Education\'', final.sql)

    def test_q78_mode_maps_directly_to_group_order_and_limit(self) -> None:
        cqd = {
            "populations": [{"entity": "schools or educational institutions",
                             "location": "city of Adelanto"}],
            "predicates": {"atoms": []},
            "aggregations": [{"type": "mode", "target": "grade span served"}],
            "outputs": [{"description": "most common grade span served"}],
        }
        catalog = {"tables": [{
            "name": "schools",
            "columns": [
                {"name": "GSserved", "description": "grade span served"},
                {"name": "GSoffered", "description": "grade span offered"},
                {"name": "City"}, {"name": "MailCity"},
            ],
            "question_value_matches": [
                {"column": "City", "value": "Adelanto"},
                {"column": "MailCity", "value": "Adelanto"},
            ], "foreign_keys": [],
        }]}
        first = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=None,
        )
        feedback = []
        for issue in first.issues:
            if "schools.GSserved" in issue["candidates"]:
                feedback.append(_feedback(issue, "schools.GSserved"))
            elif "schools.City" in issue["candidates"]:
                feedback.append(_feedback(issue, "schools.City"))
        final = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=None,
            technical_feedback=feedback,
        )
        self.assertEqual(final.issues, [])
        self.assertIn('GROUP BY "schools"."GSserved"', final.sql)
        self.assertIn('ORDER BY COUNT("schools"."GSserved") DESC', final.sql)
        self.assertIn("LIMIT 1", final.sql)

    def test_q87_ignores_duplicate_attribute_container_and_expands_email_family(self) -> None:
        cqd = {
            "populations": [{"population": "schools", "attributes": {
                "location_county": "San Bernardino", "location_city": "San Bernardino",
            }}],
            "predicates": {"atoms": [
                {"dimension": "location_county", "operator": "=", "value": "San Bernardino",
                 "source_text": "school located in the San Bernardino county"},
                {"dimension": "SOC", "operator": "=", "value": "62,54",
                 "source_text": "Intermediate/Middle Schools refers to SOC = 62"},
                {"dimension": "DOC", "operator": "=", "value": "54",
                 "source_text": "Unified School refers to DOC = 54"},
            ]},
            "outputs": [{"output": "valid e-mail addresses", "entity": "administrator"}],
        }
        catalog = {"tables": [{
            "name": "schools", "columns": [
                {"name": "AdmEmail1", "description": "administrator email address"},
                {"name": "AdmEmail2"}, {"name": "County"}, {"name": "SOC"}, {"name": "DOC"},
            ], "question_value_matches": [
                {"column": "County", "value": "San Bernardino"},
            ], "foreign_keys": [],
        }]}
        evidence = "Intermediate/Middle Schools refers to SOC = 62; Unified School refers to DOC = 54"
        first = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=evidence,
        )
        issue = next(item for item in first.issues if item["surface"] == "valid e-mail addresses")
        all_fields = "Use all sibling fields: schools.AdmEmail1, schools.AdmEmail2"
        final = compile_cqd_directly_to_sql(
            confirmed_cqd=cqd, schema_catalog=catalog, benchmark_evidence=evidence,
            technical_feedback=[_feedback(issue, all_fields)],
        )
        self.assertEqual(final.issues, [])
        self.assertIn('SELECT "schools"."AdmEmail1", "schools"."AdmEmail2"', final.sql)
        self.assertIn('"schools"."SOC" = 62', final.sql)
        self.assertNotIn("attributes", final.sql)


if __name__ == "__main__":
    unittest.main()
