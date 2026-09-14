import unittest

from aap_mcp import (
    apply_launch_template_id,
    extract_template_id,
    inject_thread_id_into_arguments,
    launch_template_id,
    quoted_name_from_step,
)


class InjectThreadIdTests(unittest.TestCase):
    def test_merges_into_request_body_extra_vars(self) -> None:
        args = {
            "id": "123",
            "request_body": {"extra_vars": {"foo": "bar"}},
        }
        result = inject_thread_id_into_arguments(
            args,
            tool_name="workflow_job_templates_launch_create",
            thread_id="thread-abc",
        )
        self.assertEqual(
            result["request_body"]["extra_vars"],
            {"foo": "bar", "thread_id": "thread-abc"},
        )
        self.assertNotIn("thread_id", args["request_body"]["extra_vars"])

    def test_merges_into_request_body_camel_case(self) -> None:
        args = {
            "id": "113",
            "requestBody": {
                "extra_vars": {
                    "vm_name": "rafa-01",
                    "cpus": 2,
                },
            },
        }
        result = inject_thread_id_into_arguments(
            args,
            tool_name="workflow_job_templates_launch_create",
            thread_id="60a3ed47-808f-4484-a09e-99b7a62ed1bb",
        )
        self.assertEqual(
            result["requestBody"]["extra_vars"],
            {
                "vm_name": "rafa-01",
                "cpus": "2",
                "thread_id": "60a3ed47-808f-4484-a09e-99b7a62ed1bb",
            },
        )
        self.assertNotIn("request_body", result)

    def test_does_not_overwrite_existing_thread_id(self) -> None:
        args = {
            "request_body": {"extra_vars": {"thread_id": "custom"}},
        }
        result = inject_thread_id_into_arguments(
            args,
            tool_name="job_templates_launch_create",
            thread_id="thread-abc",
        )
        self.assertEqual(result["request_body"]["extra_vars"]["thread_id"], "custom")

    def test_creates_request_body_for_launch_tools(self) -> None:
        result = inject_thread_id_into_arguments(
            {"id": "99"},
            tool_name="workflow_job_templates_launch_create",
            thread_id="thread-abc",
        )
        self.assertEqual(
            result,
            {
                "id": "99",
                "request_body": {"extra_vars": {"thread_id": "thread-abc"}},
            },
        )

    def test_ignores_non_launch_tools(self) -> None:
        args = {"id": "99"}
        result = inject_thread_id_into_arguments(
            args,
            tool_name="jobs_retrieve",
            thread_id="thread-abc",
        )
        self.assertEqual(result, args)

    def test_merges_top_level_extra_vars(self) -> None:
        result = inject_thread_id_into_arguments(
            {"extra_vars": {"region": "eu"}},
            tool_name="jobs_retrieve",
            thread_id="thread-abc",
        )
        self.assertEqual(
            result["extra_vars"],
            {"region": "eu", "thread_id": "thread-abc"},
        )

    def test_stringifies_numeric_extra_vars(self) -> None:
        result = inject_thread_id_into_arguments(
            {
                "id": "123",
                "request_body": {"extra_vars": {"cpus": 2, "memory_gb": 4.0}},
            },
            tool_name="job_templates_launch_create",
            thread_id="thread-abc",
        )
        self.assertEqual(
            result["request_body"]["extra_vars"],
            {"cpus": "2", "memory_gb": "4.0", "thread_id": "thread-abc"},
        )

    def test_stringifies_extra_vars_without_thread_id(self) -> None:
        result = inject_thread_id_into_arguments(
            {"request_body": {"extra_vars": {"cpus": 2}}},
            tool_name="workflow_job_templates_launch_create",
            thread_id=None,
        )
        self.assertEqual(result["request_body"]["extra_vars"], {"cpus": "2"})

    def test_folds_top_level_procedure_fields_into_extra_vars(self) -> None:
        result = inject_thread_id_into_arguments(
            {
                "vm_name": "rafa-01",
                "cpus": "1",
                "mem": "1",
                "app_repo": "http://rafa.repo.git",
                "itsm_change_ref": "CHG-15",
                "itsm_service_request_ref": "REQ-2026-016",
                "request_body": {"extra_vars": {"thread_id": "thread-abc"}},
            },
            tool_name="workflow_job_templates_launch_create",
            thread_id="thread-abc",
        )
        extra = result["request_body"]["extra_vars"]
        self.assertEqual(extra["vm_name"], "rafa-01")
        self.assertEqual(extra["cpus"], "1")
        self.assertEqual(extra["itsm_change_ref"], "CHG-15")
        self.assertEqual(extra["itsm_service_request_ref"], "REQ-2026-016")
        self.assertEqual(extra["thread_id"], "thread-abc")
        self.assertNotIn("vm_name", result)
        self.assertNotIn("app_repo", result)


class LaunchTemplateIdTests(unittest.TestCase):
    def test_prefers_derived_template_id(self) -> None:
        result = apply_launch_template_id(
            {"vm_name": "rafa-01"},
            derived={"template_id": "113"},
        )
        self.assertEqual(result["id"], "113")
        self.assertEqual(launch_template_id(result), "113")

    def test_drops_name_like_id(self) -> None:
        result = apply_launch_template_id(
            {"id": "Deploy Generic Application Stack"},
            derived={},
        )
        self.assertFalse(launch_template_id(result))

    def test_extracts_id_by_template_name(self) -> None:
        result = {
            "results": [
                {"id": 8, "name": "Other"},
                {"id": 113, "name": "Deploy Generic Application Stack"},
            ]
        }
        name = quoted_name_from_step(
            'Search the workflow job template called "Deploy Generic Application Stack"'
        )
        self.assertEqual(name, "Deploy Generic Application Stack")
        self.assertEqual(extract_template_id(result, name), "113")


if __name__ == "__main__":
    unittest.main()
