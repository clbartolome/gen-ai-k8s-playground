import unittest

from step_outputs import (
    extract_derived_from_tool_result,
    extract_template_name_from_step,
    format_launch_failure,
    inject_trusted_derived_into_launch_args,
    launch_result_is_failure,
    recover_aap_launch_decision,
    should_skip_duplicate_launch,
    validate_launch_references,
)


class StepOutputsTests(unittest.TestCase):
    def test_extract_create_request_refs(self) -> None:
        result = {
            "number": "REQ-2026-036",
            "change_ref": "CHG-20",
            "ritm_ref": "RITM-28",
        }
        derived = extract_derived_from_tool_result("create_request", result)
        self.assertEqual(derived["itsm_service_request_ref"], "REQ-2026-036")
        self.assertEqual(derived["itsm_change_ref"], "CHG-20")
        self.assertEqual(derived["itsm_ritm_ref"], "RITM-28")

    def test_extract_workflow_job_id(self) -> None:
        derived = extract_derived_from_tool_result(
            "workflow_job_templates_launch_create",
            {"workflow_job": 5409, "id": 5409},
        )
        self.assertEqual(derived["workflow_job_id"], "5409")

    def test_inject_trusted_derived_overrides_invented_ref(self) -> None:
        args = inject_trusted_derived_into_launch_args(
            {
                "id": "Deploy Generic Application Stack",
                "request_body": {
                    "extra_vars": {
                        "Application Repository": "http://gitea.svc.cluster.local:3000/giteaadmin/",
                        "Virtual Machine memory": "1",
                        "Virtual Machine name": "qr-01",
                        "Virtual Machine number of CPUs": "1",
                        "app_repo": "http://gitea.svc.cluster.local:3000/giteaadmin/",
                        "cpus": 1,
                        "itsm_change_ref": "CHG-22",
                        "itsm_ritm_ref": "RITM-31",
                        "itsm_service_request_ref": "ABC123",
                        "mem": 1,
                        "vm_name": "qr-01",
                        "thread_id": "thread-1",
                    },
                },
            },
            {
                "itsm_service_request_ref": "REQ-2026-040",
                "itsm_change_ref": "CHG-22",
            },
            known_parameters={
                "vm_name": "qr-01",
                "cpus": "1",
                "mem": "1",
                "app_repo": "http://gitea.svc.cluster.local:3000/giteaadmin/",
            },
        )
        extra_vars = args["requestBody"]["extra_vars"]
        self.assertEqual(extra_vars["itsm_service_request_ref"], "REQ-2026-040")
        self.assertEqual(extra_vars["itsm_change_ref"], "CHG-22")
        self.assertEqual(extra_vars["vm_name"], "qr-01")
        self.assertEqual(extra_vars["cpus"], "1")
        self.assertEqual(extra_vars["mem"], "1")
        self.assertEqual(
            extra_vars["app_repo"],
            "http://gitea.svc.cluster.local:3000/giteaadmin/",
        )
        self.assertEqual(extra_vars["thread_id"], "thread-1")
        self.assertNotIn("Virtual Machine name", extra_vars)
        self.assertNotIn("Application Repository", extra_vars)

    def test_canonicalize_human_labels_from_polluted_extra_vars(self) -> None:
        from parameter_coercion import canonicalize_launch_parameters

        procedure = [
            {
                "detail": (
                    "Launch workflow passing the following extra_vars:\n"
                    "vm_name: Virtual Machine name\n"
                    "cpus: Virtual Machine number of CPUs\n"
                    "mem: Virtual Machine memory\n"
                    "app_repo: Application Repository"
                ),
            },
        ]
        canonical = canonicalize_launch_parameters(
            {},
            procedure=procedure,
            polluted_extra_vars={
                "Virtual Machine name": "qr-01",
                "Virtual Machine number of CPUs": "1",
                "Virtual Machine memory": "1",
                "Application Repository": "http://gitea.svc.cluster.local:3000/giteaadmin/",
            },
        )
        self.assertEqual(canonical["vm_name"], "qr-01")
        self.assertEqual(canonical["cpus"], "1")
        self.assertEqual(canonical["mem"], "1")
        self.assertEqual(
            canonical["app_repo"],
            "http://gitea.svc.cluster.local:3000/giteaadmin/",
        )

    def test_validate_launch_rejects_unverified_ticket_ref(self) -> None:
        error = validate_launch_references(
            {
                "request_body": {
                    "extra_vars": {"itsm_service_request_ref": "ABC123"},
                },
            },
            {"itsm_service_request_ref": "REQ-2026-036"},
        )
        self.assertIsNotNone(error)
        self.assertIn("ABC123", error or "")

    def test_extract_template_name_from_step(self) -> None:
        detail = (
            'Launch the Ansible Automation Platform "Deploy Generic Application Stack" '
            "workflow job template passing the following extra_vars"
        )
        self.assertEqual(
            extract_template_name_from_step(detail),
            "Deploy Generic Application Stack",
        )

    def test_recover_aap_launch_decision(self) -> None:
        decision = recover_aap_launch_decision(
            {
                "detail": (
                    'Launch the "Deploy Generic Application Stack" workflow job template '
                    "passing vm_name, cpus, mem, app_repo and itsm_service_request_ref"
                ),
            },
            accumulated={
                "derived": {
                    "itsm_service_request_ref": "REQ-2026-036",
                    "itsm_change_ref": "CHG-20",
                },
            },
            known_parameters={
                "vm_name": "qr-01",
                "cpus": "1",
                "mem": "1",
                "app_repo": "http://example/repo.git",
            },
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["action"], "workflow_job_templates_launch_create")
        self.assertEqual(decision["arguments"]["id"], "Deploy Generic Application Stack")
        extra_vars = decision["arguments"]["requestBody"]["extra_vars"]
        self.assertEqual(extra_vars["itsm_service_request_ref"], "REQ-2026-036")
        self.assertEqual(extra_vars["mem"], "1")

    def test_should_skip_duplicate_launch_after_successful_job(self) -> None:
        accumulated = {
            "derived": {"workflow_job_id": "5456"},
            "steps_log": [],
        }
        self.assertTrue(
            should_skip_duplicate_launch(
                "workflow_job_templates_launch_create",
                accumulated,
                arguments={"id": "123456"},
            )
        )

    def test_should_not_skip_when_prior_launch_failed(self) -> None:
        accumulated = {
            "derived": {},
            "steps_log": [
                {
                    "step": 2,
                    "tool": "workflow_job_templates_launch_create",
                    "ok": False,
                    "arguments": {"id": "141"},
                },
            ],
        }
        self.assertFalse(
            should_skip_duplicate_launch(
                "workflow_job_templates_launch_create",
                accumulated,
                arguments={"id": "141"},
            )
        )

    def test_launch_result_is_failure_detects_missing_variables(self) -> None:
        self.assertTrue(
            launch_result_is_failure(
                "workflow_job_templates_launch_create",
                {"variables_needed_to_start": ["'vm_name' value missing"]},
            )
        )

    def test_launch_result_is_failure_detects_missing_template(self) -> None:
        self.assertTrue(
            launch_result_is_failure(
                "workflow_job_templates_launch_create",
                {"detail": "No WorkflowJobTemplate matches the given query."},
            )
        )

    def test_launch_result_is_failure_accepts_created_job(self) -> None:
        self.assertFalse(
            launch_result_is_failure(
                "workflow_job_templates_launch_create",
                {"workflow_job": 5456, "id": 5456, "type": "workflow_job"},
            )
        )

    def test_format_launch_failure(self) -> None:
        message = format_launch_failure(
            {"variables_needed_to_start": ["'vm_name' value missing"]},
        )
        self.assertIn("vm_name", message)


if __name__ == "__main__":
    unittest.main()
