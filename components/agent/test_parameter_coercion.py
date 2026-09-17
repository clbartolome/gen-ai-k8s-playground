import unittest

from parameter_coercion import (
    build_launch_field_aliases,
    build_parameter_specs,
    canonicalize_launch_parameters,
    coerce_known_parameters,
    coerce_parameter_value,
    extract_launch_aliases_from_procedure,
)


class ParameterCoercionTests(unittest.TestCase):
    def test_coerce_memory_with_unit(self) -> None:
        self.assertEqual(
            coerce_parameter_value(
                "mem",
                "1 GiB of memory",
                detail="Amount of memory in GiB",
            ),
            "1",
        )

    def test_coerce_cpus(self) -> None:
        self.assertEqual(
            coerce_parameter_value("cpus", "1 cpu", detail="Virtual Machine number of CPUs"),
            "1",
        )

    def test_preserves_repo_url(self) -> None:
        url = "http://gitea.svc.cluster.local:3000/giteaadmin/AIOps_App.git"
        self.assertEqual(
            coerce_parameter_value("app_repo", url, detail="Application Repository"),
            url,
        )

    def test_coerce_known_parameters(self) -> None:
        specs = build_parameter_specs(
            [{"name": "mem", "detail": "Amount of memory in GiB"}],
            [{"name": "vm_name", "value": "qr-01"}, {"name": "mem", "value": "1 GiB"}],
        )
        result = coerce_known_parameters(
            [
                {"name": "vm_name", "value": "qr-01"},
                {"name": "mem", "value": "1 GiB"},
            ],
            specs,
        )
        by_name = {item["name"]: item["value"] for item in result}
        self.assertEqual(by_name["mem"], "1")
        self.assertEqual(by_name["vm_name"], "qr-01")

    def test_extract_launch_aliases_from_procedure(self) -> None:
        aliases = extract_launch_aliases_from_procedure(
            [
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
        )
        self.assertEqual(aliases["virtual machine name"], "vm_name")
        self.assertEqual(aliases["application repository"], "app_repo")

    def test_build_launch_field_aliases_from_parameter_specs(self) -> None:
        aliases = build_launch_field_aliases(
            [
                {"name": "vm_name", "detail": "Virtual Machine name"},
                {"name": "app_repo", "detail": "Application Repository"},
            ],
            procedure=[],
        )
        self.assertEqual(aliases["virtual machine name"], "vm_name")
        self.assertEqual(aliases["application repository"], "app_repo")

    def test_canonicalize_launch_parameters_from_procedure(self) -> None:
        canonical = canonicalize_launch_parameters(
            {},
            procedure=[
                {
                    "detail": (
                        "Launch workflow passing the following extra_vars:\n"
                        "vm_name: Virtual Machine name\n"
                        "cpus: Virtual Machine number of CPUs\n"
                        "mem: Virtual Machine memory\n"
                        "app_repo: Application Repository"
                    ),
                },
            ],
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

    def test_canonicalize_without_article_metadata_keeps_unknown_keys(self) -> None:
        canonical = canonicalize_launch_parameters(
            {"custom_field": "value"},
            polluted_extra_vars={"Some custom label": "ignored"},
        )
        self.assertEqual(canonical["custom_field"], "value")
        self.assertNotIn("Some custom label", canonical)


if __name__ == "__main__":
    unittest.main()
