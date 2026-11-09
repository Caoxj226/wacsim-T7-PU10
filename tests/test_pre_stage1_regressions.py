"""Regression checks for the CRAVE platform-adapter work before Stage 1 runs."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from evaluation.rq1 import rq1_eval
from evaluation.rq2 import rq2_eval
from evaluation.rq4 import rq4_eval
from simulators.wacsim_ctown import runtime_executor as wacsim_executor
from src.platforms.variable_mapping import build_platform_variable_map
from src.stage1 import hazard_ranking, semantic_extraction, sensitivity_matrix
from src.stage2 import base_search, conditional_search
from src.stage3 import subregions


def _extraction(platform: str) -> dict:
    directory = ROOT / "simulators" / platform
    return semantic_extraction.build_extraction(
        controller_path=directory / "controller.py",
        simulation_path=directory / "simulation.py",
        variable_registry=build_platform_variable_map(ROOT, platform),
        manifest_path=directory / "system_manifest.json",
    )


def _payload_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SemanticCompatibilityTests(unittest.TestCase):
    def test_wacsim_alarm_threshold_is_the_formal_calibration_lock_value(self) -> None:
        manifest = json.loads((ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json").read_text())
        lock = yaml.safe_load(
            (ROOT / "third_party" / "WaCSim" / "examples" / "crave_ctown" / "calibration.lock.yaml").read_text()
        )
        alarm = next(rule for rule in manifest["alarm_rules"] if rule["id"] == "A-T7-TRACK")
        self.assertNotIn("threshold", alarm)
        self.assertEqual(alarm["threshold_source"]["key"], "theta_m")
        extraction = _extraction("wacsim_ctown")
        self.assertEqual(extraction["P"][0]["threshold"], lock["theta_m"])
        self.assertEqual(
            extraction["P"][0]["threshold_source_resolved"]["sha256"],
            alarm["threshold_source"]["sha256"],
        )

    def test_original_platform_extractions_remain_exact(self) -> None:
        expected = {
            "boiler_ccs": "961777707961a688e9e9545840eeab06508cedeea57cc1eb6379808e12cbcd24",
            "tennessee_eastman": "a0d0608c4486cce042c0de3ca7923724b97e8dd63d72ef9cd957bd71c1d60542",
        }
        for platform, expected_sha in expected.items():
            with self.subTest(platform=platform):
                self.assertEqual(_payload_sha256(_extraction(platform)), expected_sha)

    def test_wacsim_uses_only_the_reviewed_three_semantic_edges(self) -> None:
        payload = _extraction("wacsim_ctown")
        edges = {(edge["x"], edge["y"]) for edge in payload["G"]["E"]}
        self.assertEqual(
            edges,
            {
                ("T7_LEVEL_SP", "PU10_SPEED_CMD"),
                ("T7_LEVEL", "PU10_SPEED_CMD"),
                ("PU10_SPEED_CMD", "T7_LEVEL"),
            },
        )


class ScoreBoundaryTests(unittest.TestCase):
    def _probe(self) -> dict:
        return {
            "status": "ok",
            "rows": [
                {"t_step": 8, "alarm_rule_ids": ["A-T7-TRACK"], "hazard_rule_ids": [], "alarm_margin_m": -0.1},
                {"t_step": 24, "alarm_rule_ids": [], "hazard_rule_ids": [], "alarm_margin_m": 0.0},
                {"t_step": 25, "alarm_rule_ids": [], "hazard_rule_ids": ["H-T7-LOW"], "alarm_margin_m": 0.1},
            ],
            "score": {"score_start_step": 24, "first_hazard_step": 25, "prehazard_alarm_rule_ids": []},
            "_rules": {
                "alarms": {"A-T7-TRACK": {"margin_col": "alarm_margin_m", "margin_trigger": "negative"}},
                "hazards": {"H-T7-LOW": {"margin_col": "hazard_margin_m", "margin_trigger": "nonpositive"}},
            },
        }

    def test_base_search_excludes_warmup_and_respects_strict_alarm_margin(self) -> None:
        probe = self._probe()
        self.assertFalse(base_search._has_prehazard_alarm(probe, "A-T7-TRACK", 25))
        self.assertEqual(base_search._prehazard_alarm_ids(probe, 25), set())

    def test_rq1_excludes_warmup_and_respects_strict_alarm_margin(self) -> None:
        replay = rq1_eval._classify_w_probe(self._probe())
        self.assertTrue(replay["hazard_reached"])
        self.assertEqual(replay["pre_hazard_alarm_ids"], [])
        self.assertIsNone(replay["first_alarm_time"])

    def test_failed_probe_is_not_a_normal_untarget_probe(self) -> None:
        self.assertEqual(
            base_search._classify_probe({"status": "error", "rows": [], "score": {}}, "H-T7-LOW", "A-T7-TRACK"),
            "error",
        )


class AdapterContractTests(unittest.TestCase):
    def test_registry_exposes_the_reviewed_full_slope_interval(self) -> None:
        manifest = ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json"
        executor = wacsim_executor.create_executor(manifest)
        self.assertEqual(base_search._amp_range_from_registry(executor, "PU10_SPEED_CMD"), (0.001, 0.05))

    def test_adapter_rejects_malformed_or_silently_changed_inputs(self) -> None:
        with self.assertRaises(ValueError):
            wacsim_executor.normalize_injection_points({"points": [{}, "not-an-object"]})

        class DummyInjectionSpec:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        manifest = ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json"
        registry = wacsim_executor.load_injection_registry(manifest)
        for invalid in (
            {"point": "PU10_SPEED_CMD", "amp": -0.01, "duration": 5.5, "t_start": 0},
            {"point": "PU10_SPEED_CMD", "amp": -0.01, "duration": 5, "t_start": 1},
            {"point": "PU10_SPEED_CMD", "amp": -0.01, "duration": 5, "t_start": 0, "range": [0.0, 2.0]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                wacsim_executor._build_specs([invalid], registry, DummyInjectionSpec)

    def test_sensitivity_compares_equal_total_horizons(self) -> None:
        calls: list[dict] = []

        def fake_executor(task: dict) -> dict:
            calls.append(task)
            return {"status": "ok", "rows": [{"T7_LEVEL": 1.0}], "score": {}}

        fake_executor.registry = {"PU10_SPEED_CMD": {"rate": 0.001, "default_direction": "neg"}}
        fake_executor.warmup_steps = 24
        extraction = {"H": [{"id": "H-T7-LOW", "var": "T7_LEVEL", "jacobian_col": "T7_LEVEL"}]}
        with patch.object(sensitivity_matrix, "_build_executor", return_value=fake_executor):
            output = sensitivity_matrix.build_hazard_jacobian(ROOT, "wacsim_ctown", extraction, duration=5)
        self.assertEqual(output["total_steps"], 129)
        self.assertEqual([call["runtime_args"]["steps"] for call in calls], [129, 129])
        self.assertEqual(output["admitted_directions"], {"PU10_SPEED_CMD": "decrease"})

    def test_ranking_uses_reviewed_direction_when_closed_loop_endpoint_recovers(self) -> None:
        extraction = {
            "W": [{"canonical_name": "PU10_SPEED_CMD", "layer": "actuator"}],
            "G": {"E": [{"x": "PU10_SPEED_CMD", "y": "T7_LEVEL"}]},
            "H": [{"id": "H-T7-LOW", "var": "T7_LEVEL", "direction": "lower"}],
        }
        jacobian = {
            "platform": "wacsim_ctown",
            "rows": ["PU10_SPEED_CMD"],
            "hazard_columns": {"H-T7-LOW": {"col": "T7_LEVEL", "kind": "state"}},
            "matrix": {"PU10_SPEED_CMD": {"T7_LEVEL": -10.0}},
            "admitted_directions": {"PU10_SPEED_CMD": "decrease"},
        }
        item = hazard_ranking.rank_hazard_drivers(extraction, jacobian)["rankings"]["H-T7-LOW"][0]
        self.assertEqual(item["input_direction"], "decrease")
        self.assertEqual(item["input_direction_source"], "reviewed_injection_contract")
        self.assertEqual(item["endpoint_derivative_direction"], "increase")

    def test_legacy_platform_ranking_keeps_derivative_direction_and_schema(self) -> None:
        extraction = {
            "W": [{"canonical_name": "legacy_driver", "layer": "actuator"}],
            "G": {"E": [{"x": "legacy_driver", "y": "legacy_state"}]},
            "H": [{"id": "H-LOW", "var": "legacy_state", "direction": "lower"}],
        }
        jacobian = {
            "platform": "legacy",
            "rows": ["legacy_driver"],
            "hazard_columns": {"H-LOW": {"col": "legacy_state", "kind": "state"}},
            "matrix": {"legacy_driver": {"legacy_state": -10.0}},
        }
        item = hazard_ranking.rank_hazard_drivers(extraction, jacobian)["rankings"]["H-LOW"][0]
        self.assertEqual(item["input_direction"], "increase")
        self.assertNotIn("input_direction_source", item)
        self.assertNotIn("endpoint_derivative_direction", item)

    def test_timeout_contract_is_finite_and_bounded(self) -> None:
        manifest = ROOT / "simulators" / "wacsim_ctown" / "system_manifest.json"
        self.assertEqual(wacsim_executor._timeout_seconds({"runtime_args": {}}, manifest), 120.0)
        with self.assertRaises(ValueError):
            wacsim_executor._timeout_seconds({"runtime_args": {"timeout_seconds": 0}}, manifest)


class PlatformParameterizationTests(unittest.TestCase):
    def test_wacsim_manifest_mapping_and_stage2_to_stage3_contract(self) -> None:
        variable_map = build_platform_variable_map(ROOT, "wacsim_ctown")
        records = {item["canonical_name"]: item for item in variable_map}
        self.assertEqual(set(records), {"J317_PRESSURE", "PU10_FLOW", "PU10_SPEED_CMD", "T7_LEVEL", "T7_LEVEL_SP"})
        self.assertEqual(records["PU10_SPEED_CMD"]["roles"], ["injectable"])
        self.assertEqual(records["T7_LEVEL_SP"]["roles"], ["injectable"])

        class FakeExecutor:
            registry = {"PU10_SPEED_CMD": {"slope_magnitude_range": [0.001, 0.05]}}
            rules = {"alarms": {"A-T7-TRACK": {"margin_trigger": "negative"}}}

            def __call__(self, task: dict) -> dict:
                amp = abs(float(task["injection"]["amp"]))
                targeted = amp >= 0.02
                return {
                    "status": "ok",
                    "run_key": f"rk_{amp:.6f}",
                    "rows": [
                        {"t_step": 0, "alarm_rule_ids": ["A-T7-TRACK"] if targeted else [], "alarm_margin_m": -1.0 if targeted else 1.0, "hazard_rule_ids": []},
                        {"t_step": 1, "alarm_rule_ids": [], "hazard_rule_ids": ["H-T7-LOW"] if targeted else []},
                    ],
                    "score": {"score_start_step": 0, "first_hazard_step": 1 if targeted else None},
                }

        boundary = base_search.run_base_search(
            executor=FakeExecutor(),
            platform="wacsim_ctown",
            hazard_driver="PU10_SPEED_CMD",
            hazard_id="H-T7-LOW",
            alarm_id="A-T7-TRACK",
            durations=[25],
            amp_magnitude_range=(0.001, 0.05),
            directions=("neg",),
            coarse_span=0.01,
            amp_tol=0.005,
            workers=1,
        )
        interval = boundary["boundaries"][0]["target_intervals"][0]
        self.assertLessEqual(interval["rate_lo"], 0.025)
        extraction = _extraction("wacsim_ctown")
        conditional = {
            "points": [{
                "point_id": "p0", "duration": 25, "runtime_steps": 125,
                "hazard_driver": "PU10_SPEED_CMD", "hazard_id": "H-T7-LOW",
                "alarm_id": "A-T7-TRACK", "driver_amp_range": [interval["rate_lo"], interval["rate_hi"]],
                "stable_slope_range": [-0.05, -0.04], "extreme_slope_range": [-0.05, -0.04],
            }],
        }
        points = subregions._build_points(
            {"platform": "wacsim_ctown", "boundaries": boundary["boundaries"]},
            conditional,
            extraction,
            {},
            "H-T7-LOW",
            "auto",
            1,
            25,
        )
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["masking_variable"], "T7_LEVEL_SP")
        self.assertEqual(points[0]["driver_direction"], "neg")

    def test_rq2_wacsim_case_controls_domain_budget_direction_and_output_name(self) -> None:
        boundary = {
            "platform": "wacsim_ctown",
            "boundaries": [{
                "direction": "neg",
                "duration": 5,
                "hazard_driver": "PU10_SPEED_CMD",
                "hazard_id": "H-T7-LOW",
                "alarm_id": "A-T7-TRACK",
                "target_intervals": [{"rate_lo": 0.001, "rate_hi": 0.05}],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary_path = root / "boundary.json"
            boundary_path.write_text(json.dumps(boundary), encoding="utf-8")
            result = rq2_eval.run_rq2_evaluation(
                root=root,
                platform="wacsim",
                boundary_path=boundary_path,
                run_baselines=False,
            )
            reference = result["reference"]
            self.assertEqual(reference["duration_range"], [5, 120])
            self.assertEqual(reference["rate_range"], [0.001, 0.05])
            self.assertEqual(reference["direction"], "neg")
            self.assertEqual(reference["crave_budget"], 2939)
            self.assertTrue((root / "results" / "rq2" / "wacsim_ctown_ablation_results.json").exists())
            self.assertFalse((root / "results" / "rq2" / "te_ablation_results.json").exists())
        task = rq2_eval._probe_task(
            hazard_driver="PU10_SPEED_CMD",
            duration=5,
            rate=rq2_eval._signed_rate(0.01, "neg"),
            stop_ignore_predicates=[],
        )
        self.assertEqual(task["injection"]["amp"], -0.01)

    def test_conditional_search_retains_reviewed_negative_masker_domain(self) -> None:
        self.assertEqual(conditional_search._mask_slope_range(
            type("Executor", (), {"registry": {"T7_LEVEL_SP": {"slope_range_per_step": [-0.05, -0.001]}}})(),
            "T7_LEVEL_SP",
        ), (-0.05, -0.001))
        grid = conditional_search._slope_grid((-0.05, -0.001))
        self.assertEqual(grid[0], -0.05)
        self.assertAlmostEqual(grid[-1], -0.001)

    def test_stage3_prefers_explicit_signed_masker_domain(self) -> None:
        executor = type("Executor", (), {"registry": {"T7_LEVEL_SP": {"rate": 0.001, "slope_range_per_step": [-0.05, -0.001]}}})()
        point = {"masking_variable": "T7_LEVEL_SP"}
        self.assertEqual(subregions._registry_slope_bounds(executor, point), [-0.05, -0.001])
        self.assertEqual(subregions._slope_domain(executor.registry["T7_LEVEL_SP"]), (-0.05, -0.001))

    def test_rq1_wacsim_plan_excludes_masker_only_probe(self) -> None:
        plan = rq1_eval._scan_plan("wacsim_ctown", None, None, None)
        self.assertEqual(plan["duration_values"], [5, 30, 60, 90, 120])
        self.assertEqual(plan["directions"], ["neg"])

        calls: list[dict] = []

        class FakeExecutor:
            registry = {
                "PU10_SPEED_CMD": {"role": "driver"},
                "T7_LEVEL_SP": {"role": "masker"},
            }

            def __call__(self, task: dict) -> dict:
                calls.append(task)
                return {
                    "status": "ok",
                    "rows": [{"t_step": 24, "hazard_rule_ids": ["H-T7-LOW"], "alarm_rule_ids": []}],
                    "score": {"score_start_step": 24, "first_hazard_step": 24},
                }

        scan = rq1_eval.run_w_scan_for_platform(
            root=ROOT,
            platform="wacsim_ctown",
            extraction=_extraction("wacsim_ctown"),
            ranking={"candidate_rankings": {"H-T7-LOW": [
                {"variable": "PU10_SPEED_CMD"}, {"variable": "T7_LEVEL_SP"},
            ]}},
            duration_values=plan["duration_values"],
            scan_rates=plan["scan_rates"],
            directions=plan["directions"],
            rank_top_k=3,
            executor_factory=lambda _path: FakeExecutor(),
            admitted_roles=plan["admitted_roles"],
            excluded_role_reason=plan["excluded_role_reason"],
        )
        self.assertTrue(calls)
        self.assertTrue(all(task["injection"]["point"] == "PU10_SPEED_CMD" for task in calls))
        excluded = next(row for row in scan["rows"] if row["writable_id"] == "T7_LEVEL_SP")
        self.assertEqual(excluded["admission_status"], "excluded")
        self.assertTrue(excluded["exclusion_reason"])

    def test_rq4_resolves_wacsim_boundary_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = rq4_eval.run_rq4_evaluation(
                root=ROOT,
                rq4_dir=Path(directory),
                boundary_platform="wacsim_ctown",
                partition_platform="wacsim_ctown",
            )
        boundary = payload["config"]["boundary_sensitivity"]
        self.assertEqual(boundary["platform"], "wacsim_ctown")
        self.assertEqual(boundary["directions"], ["neg"])
        self.assertEqual(boundary["rate_range"], [0.001, 0.05])


if __name__ == "__main__":
    unittest.main()
