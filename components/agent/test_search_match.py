import unittest

from search_match import (
    extract_item_id_by_exact_name,
    quoted_name_from_step,
    step_requires_exact_search_match,
)


class SearchMatchTests(unittest.TestCase):
    def test_quoted_name_from_step(self) -> None:
        detail = 'Search the workflow job template called "Deploy Generic Application Stack"'
        self.assertEqual(
            quoted_name_from_step(detail),
            "Deploy Generic Application Stack",
        )

    def test_exact_name_match(self) -> None:
        result = {
            "results": [
                {"id": 8, "name": "Other"},
                {"id": 113, "name": "Deploy Generic Application Stack"},
            ]
        }
        name = quoted_name_from_step(
            'Search the workflow job template called "Deploy Generic Application Stack"'
        )
        self.assertEqual(extract_item_id_by_exact_name(result, name), "113")

    def test_rejects_partial_name_match(self) -> None:
        result = {
            "results": [
                {"id": 113, "name": "Deploy Generic Application Stack"},
            ]
        }
        self.assertEqual(
            extract_item_id_by_exact_name(result, "Deploy Generic"),
            "",
        )

    def test_rejects_case_insensitive_match(self) -> None:
        result = {
            "results": [
                {"id": 113, "name": "Deploy Generic Application Stack"},
            ]
        }
        self.assertEqual(
            extract_item_id_by_exact_name(result, "deploy generic application stack"),
            "",
        )

    def test_requires_name_to_match(self) -> None:
        result = {
            "results": [
                {"id": 113, "name": "Deploy Generic Application Stack"},
            ]
        }
        self.assertEqual(extract_item_id_by_exact_name(result, ""), "")
        self.assertEqual(extract_item_id_by_exact_name(result, None), "")

    def test_matches_metadata_name(self) -> None:
        result = {
            "items": [
                {"metadata": {"name": "production", "uid": "abc-123"}},
            ]
        }
        self.assertEqual(
            extract_item_id_by_exact_name(result, "production"),
            "abc-123",
        )

    def test_step_requires_exact_search_match(self) -> None:
        detail = 'List namespaces and select "app-prod"'
        self.assertTrue(
            step_requires_exact_search_match(
                detail,
                "namespaces_list",
            )
        )
        self.assertFalse(
            step_requires_exact_search_match(
                'Launch workflow "Deploy Generic Application Stack"',
                "workflow_job_templates_launch_create",
            )
        )


if __name__ == "__main__":
    unittest.main()
