import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from aap_mcp import (
    AapMcpClient,
    TemplateAmbiguousError,
    TemplateNotFoundError,
    extract_template_list,
    extract_template_reference,
    find_templates_by_name,
    inject_thread_id_into_arguments,
    is_numeric_template_id,
    normalize_launch_arguments,
    normalize_extra_var_value,
    resolve_launch_template_arguments,
    resolve_template_id_from_items,
)


SAMPLE_TEMPLATES = [
    {"id": 10, "name": "Backup Database"},
    {"id": 11, "name": "Restore Database"},
    {"id": 42, "name": "Provision VM Workflow"},
]


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


class TemplateResolverTests(unittest.TestCase):
    def test_is_numeric_template_id(self) -> None:
        self.assertTrue(is_numeric_template_id("42"))
        self.assertTrue(is_numeric_template_id(42))
        self.assertFalse(is_numeric_template_id("Backup Database"))
        self.assertFalse(is_numeric_template_id(""))
        self.assertFalse(is_numeric_template_id(True))

    def test_extract_template_list_from_results(self) -> None:
        payload = {"count": 2, "results": SAMPLE_TEMPLATES[:2]}
        self.assertEqual(extract_template_list(payload), SAMPLE_TEMPLATES[:2])

    def test_find_templates_exact_match_is_case_insensitive(self) -> None:
        matches = find_templates_by_name(SAMPLE_TEMPLATES, "backup database")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], 10)

    def test_find_templates_multiple_partial_matches(self) -> None:
        items = [
            {"id": 1, "name": "Backup Database Prod"},
            {"id": 2, "name": "Backup Database Dev"},
        ]
        matches = find_templates_by_name(items, "Backup Database")
        self.assertEqual(len(matches), 2)

    def test_find_templates_unique_partial_match(self) -> None:
        matches = find_templates_by_name(SAMPLE_TEMPLATES, "Provision VM")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], 42)

    def test_resolve_template_id_from_items(self) -> None:
        self.assertEqual(
            resolve_template_id_from_items(SAMPLE_TEMPLATES, "Backup Database"),
            10,
        )

    def test_resolve_template_id_not_found(self) -> None:
        with self.assertRaises(TemplateNotFoundError):
            resolve_template_id_from_items(SAMPLE_TEMPLATES, "Missing Template")

    def test_resolve_template_id_ambiguous(self) -> None:
        items = [
            {"id": 1, "name": "Backup Database Prod"},
            {"id": 2, "name": "Backup Database Dev"},
        ]
        with self.assertRaises(TemplateAmbiguousError):
            resolve_template_id_from_items(items, "Backup Database")

    def test_extract_template_reference_prefers_id(self) -> None:
        self.assertEqual(
            extract_template_reference({"id": "Backup Database", "name": "Other"}),
            ("id", "Backup Database"),
        )

    def test_extract_template_reference_from_name_field(self) -> None:
        self.assertEqual(
            extract_template_reference({"name": "Backup Database"}),
            ("name", "Backup Database"),
        )

    def test_resolve_launch_arguments_keeps_numeric_id(self) -> None:
        args = {"id": "42", "request_body": {"extra_vars": {"foo": "bar"}}}
        result = resolve_launch_template_arguments(
            args,
            tool_name="job_templates_launch_create",
            list_items=SAMPLE_TEMPLATES,
        )
        self.assertEqual(result["id"], "42")
        self.assertEqual(result["request_body"]["extra_vars"]["foo"], "bar")

    def test_resolve_launch_arguments_maps_name_in_id_field(self) -> None:
        args = {
            "id": "Backup Database",
            "request_body": {"extra_vars": {"env": "prod"}},
        }
        result = resolve_launch_template_arguments(
            args,
            tool_name="job_templates_launch_create",
            list_items=SAMPLE_TEMPLATES,
        )
        self.assertEqual(result["id"], "10")
        self.assertEqual(result["request_body"]["extra_vars"]["env"], "prod")

    def test_resolve_launch_arguments_maps_name_field(self) -> None:
        args = {
            "name": "Provision VM Workflow",
            "request_body": {"extra_vars": {"vm_name": "web-01"}},
        }
        result = resolve_launch_template_arguments(
            args,
            tool_name="workflow_job_templates_launch_create",
            list_items=SAMPLE_TEMPLATES,
        )
        self.assertEqual(result["id"], "42")
        self.assertNotIn("name", result)


class ExtraVarsNormalizationTests(unittest.TestCase):
    def test_normalize_integers_to_strings(self) -> None:
        self.assertEqual(
            normalize_extra_var_value({"cpus": 1, "mem": 1, "vm_name": "qr-01"}),
            {"cpus": "1", "mem": "1", "vm_name": "qr-01"},
        )

    def test_preserves_bool_and_existing_strings(self) -> None:
        self.assertEqual(
            normalize_extra_var_value({"enabled": True, "region": "eu"}),
            {"enabled": True, "region": "eu"},
        )

    def test_normalize_launch_arguments_in_request_body(self) -> None:
        args = {
            "id": "141",
            "requestBody": {
                "extra_vars": {
                    "vm_name": "qr-01",
                    "cpus": 1,
                    "mem": 1,
                },
            },
        }
        result = normalize_launch_arguments(args)
        self.assertEqual(result["requestBody"]["extra_vars"]["cpus"], "1")
        self.assertEqual(result["requestBody"]["extra_vars"]["mem"], "1")

    def test_normalize_launch_arguments_top_level(self) -> None:
        args = {"id": "99", "extra_vars": {"count": 2, "name": "demo"}}
        result = normalize_launch_arguments(args)
        self.assertEqual(result["extra_vars"]["count"], "2")


class AapMcpClientResolverTests(unittest.TestCase):
    def test_call_tool_resolves_template_name_before_launch(self) -> None:
        client = MagicMock(spec=AapMcpClient)
        client._allowlist = [
            "job_templates_list",
            "job_templates_launch_create",
        ]

        def loader(list_tool: str) -> list[dict[str, Any]]:
            self.assertEqual(list_tool, "job_templates_list")
            return SAMPLE_TEMPLATES

        resolved = AapMcpClient._resolve_launch_template_id(
            client,
            "job_templates_launch_create",
            {"id": "Backup Database", "request_body": {"extra_vars": {"x": 1}}},
            list_loader=loader,
        )
        self.assertEqual(resolved["id"], "10")

    @patch.object(AapMcpClient, "_rpc")
    @patch.object(AapMcpClient, "_ensure_session")
    @patch.object(AapMcpClient, "_normalize_tool_result")
    def test_call_tool_invokes_launch_with_resolved_id(
        self,
        normalize_mock: MagicMock,
        ensure_mock: MagicMock,
        rpc_mock: MagicMock,
    ) -> None:
        settings = MagicMock()
        settings.aap_mcp_url = "http://aap.example/mcp"
        settings.tools_timeout = 30
        settings.aap_mcp_token = "token"
        settings.aap_mcp_tool_allowlist = [
            "job_templates_list",
            "job_templates_launch_create",
        ]

        client = AapMcpClient(settings)
        normalize_mock.side_effect = [
            {"results": SAMPLE_TEMPLATES},
            {"status": "ok"},
        ]
        rpc_mock.return_value = {"content": []}

        client.call_tool(
            "job_templates_launch_create",
            {"id": "Backup Database", "request_body": {"extra_vars": {"env": "prod"}}},
        )

        launch_call = rpc_mock.call_args_list[-1]
        self.assertEqual(launch_call.args[0], "tools/call")
        params = launch_call.args[1]
        self.assertEqual(params["name"], "job_templates_launch_create")
        self.assertEqual(params["arguments"]["id"], "10")

    @patch.object(AapMcpClient, "_rpc")
    @patch.object(AapMcpClient, "_ensure_session")
    @patch.object(AapMcpClient, "_normalize_tool_result")
    def test_call_tool_normalizes_numeric_extra_vars(
        self,
        normalize_mock: MagicMock,
        ensure_mock: MagicMock,
        rpc_mock: MagicMock,
    ) -> None:
        settings = MagicMock()
        settings.aap_mcp_url = "http://aap.example/mcp"
        settings.tools_timeout = 30
        settings.aap_mcp_token = "token"
        settings.aap_mcp_tool_allowlist = [
            "workflow_job_templates_list",
            "workflow_job_templates_launch_create",
        ]

        client = AapMcpClient(settings)
        normalize_mock.return_value = {"status": "ok"}
        rpc_mock.return_value = {"content": []}

        client.call_tool(
            "workflow_job_templates_launch_create",
            {
                "id": "141",
                "request_body": {
                    "extra_vars": {
                        "vm_name": "qr-01",
                        "cpus": 1,
                        "mem": 1,
                    },
                },
            },
        )

        params = rpc_mock.call_args.args[1]
        extra_vars = params["arguments"]["request_body"]["extra_vars"]
        self.assertEqual(extra_vars["cpus"], "1")
        self.assertEqual(extra_vars["mem"], "1")


if __name__ == "__main__":
    unittest.main()
