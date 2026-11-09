"""Platform-specific contracts for the reviewed WaCSim CTown integration."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from evaluation.rq1 import rq1_eval
from simulators.wacsim_ctown import runtime_executor as wacsim_executor
from src.platforms.variable_mapping import build_platform_variable_map
from src.stage1 import semantic_extraction
from src.stage2 import base_search, conditional_search
from src.stage3 import subregions


def _extraction() -> dict:
    directory = ROOT / "simulators" / "wacsim_ctown"
    return semantic_extraction.build_extraction(
        controller_path=directory / "controller.py",
        simulation_path=directory / "simulation.py",
        variable_registry=build_platform_variable_map(ROOT, "wacsim_ctown"),
        manifest_path=directory / "system_manifest.json",
    )


class WaCSimCTownContractTests(unittest.TestCase):
    def test_manifest_lock_variable_and_rule_contract(self) -> None:
        manifest = json.loads((ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json").read_text(encoding="utf-8"))
        lock_path = ROOT / "third_party" / "WaCSim" / "examples" / "crave_ctown" / "calibration.lock.yaml"
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        alarm = next(rule for rule in manifest["alarm_rules"] if rule["id"] == "A-T7-TRACK")
        hazard = next(rule for rule in manifest["hazard_rules"] if rule["id"] == "H-T7-LOW")
        self.assertEqual({item["name"] for item in manifest["injection_points"]}, {"PU10_SPEED_CMD", "T7_LEVEL_SP"})
        self.assertEqual((hazard["var"], alarm["setpoint_var"], alarm["var"]), ("T7_LEVEL", "T7_LEVEL_SP", "T7_LEVEL"))
        self.assertEqual(alarm["threshold_source"]["sha256"], hashlib.sha256(lock_path.read_bytes()).hexdigest())
        self.assertEqual(lock["theta_m"], _extraction()["P"][0]["threshold"])

    def test_reviewed_semantic_graph_and_stage1_variables(self) -> None:
        extraction = _extraction()
        self.assertEqual({(edge["x"], edge["y"]) for edge in extraction["G"]["E"]}, {("T7_LEVEL_SP", "PU10_SPEED_CMD"), ("T7_LEVEL", "PU10_SPEED_CMD"), ("PU10_SPEED_CMD", "T7_LEVEL")})
        self.assertEqual({item["canonical_name"] for item in extraction["W"]}, {"PU10_SPEED_CMD", "T7_LEVEL_SP"})
        self.assertEqual((extraction["H"][0]["id"], extraction["P"][0]["id"]), ("H-T7-LOW", "A-T7-TRACK"))

    def test_adapter_registry_has_the_reviewed_signed_domains(self) -> None:
        executor = wacsim_executor.create_executor(ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json")
        self.assertEqual(base_search._amp_range_from_registry(executor, "PU10_SPEED_CMD"), (0.001, 0.05))
        self.assertEqual(conditional_search._mask_slope_range(executor, "T7_LEVEL_SP"), (-0.05, -0.001))
        self.assertEqual(subregions._slope_domain(executor.registry["T7_LEVEL_SP"]), (-0.05, -0.001))

    def test_executor_rejects_non_contract_injection_shapes(self) -> None:
        with self.assertRaises(ValueError):
            wacsim_executor.normalize_injection_points({"points": [{}, "not-an-object"]})

        class DummyInjectionSpec:
            def __init__(self, **kwargs: object) -> None:
                self.__dict__.update(kwargs)

        registry = wacsim_executor.load_injection_registry(ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json")
        with self.assertRaises(ValueError):
            wacsim_executor._build_specs([{"point": "PU10_SPEED_CMD", "amp": -0.01, "duration": 5, "t_start": 1}], registry, DummyInjectionSpec)

    def test_wacsim_case_excludes_masker_only_rq1_probes(self) -> None:
        plan = rq1_eval._scan_plan("wacsim_ctown", None, None, None)
        self.assertEqual((plan["directions"], plan["admitted_roles"]), (["neg"], ["driver"]))
        self.assertIn("T7_LEVEL_SP", plan["excluded_role_reason"])

    def test_final_smoke_configuration_is_the_reviewed_small_budget(self) -> None:
        config = json.loads((ROOT / "reproduction" / "configs" / "wacsim_ctown_13_8_small_budget.json").read_text(encoding="utf-8"))
        self.assertEqual(config["kind"], "third_stage_integration_smoke_not_formal_rq")
        self.assertEqual((config["stage2"]["hazard_driver"], config["stage2"]["directions"], config["stage2"]["hazard_id"], config["stage2"]["alarm_id"]), ("PU10_SPEED_CMD", ["neg"], "H-T7-LOW", "A-T7-TRACK"))


if __name__ == "__main__":
    unittest.main()
