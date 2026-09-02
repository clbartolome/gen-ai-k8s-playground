import unittest

from aap_mcp import inject_thread_id_into_arguments


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
                "cpus": 2,
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


if __name__ == "__main__":
    unittest.main()
