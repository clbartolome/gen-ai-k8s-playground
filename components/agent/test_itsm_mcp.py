import unittest

from itsm_mcp import prepare_create_request_arguments


class PrepareCreateRequestArgumentsTests(unittest.TestCase):
    def test_moves_procedure_fields_and_fills_name(self) -> None:
        result = prepare_create_request_arguments(
            {
                "vm_name": "rafa-01",
                "cpus": "1",
                "mem": "1",
                "app_repo": "http://rafa.repo.git",
            },
            step_detail=(
                'Open an ITSM service request using the **Generic-Application-Stack** '
                "ITSM template providing the following parameters."
            ),
        )
        self.assertEqual(
            result,
            {
                "name": "Deploy application on rafa-01",
                "description": "Deploy application on rafa-01",
                "request_template_id": "Generic-Application-Stack",
                "specifications_json": {
                    "vm_name": "rafa-01",
                    "cpus": "1",
                    "mem": "1",
                    "app_repo": "http://rafa.repo.git",
                },
            },
        )

    def test_keeps_explicit_name_and_nested_specs(self) -> None:
        result = prepare_create_request_arguments(
            {
                "name": "My request",
                "description": "From chat",
                "request_template_id": "Generic-Application-Stack",
                "specifications_json": {"vm_name": "rafa-01", "cpus": 2},
            }
        )
        self.assertEqual(result["name"], "My request")
        self.assertEqual(result["description"], "From chat")
        self.assertEqual(result["specifications_json"]["cpus"], "2")

    def test_is_idempotent(self) -> None:
        first = prepare_create_request_arguments(
            {"vm_name": "rafa-01", "cpus": 1},
            step_detail="using the Generic-Application-Stack ITSM template",
        )
        second = prepare_create_request_arguments(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
