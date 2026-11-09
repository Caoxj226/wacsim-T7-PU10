"""Run the bounded WaCSim CTown integration smoke pipeline.

This is deliberately a smoke test, not a formal RQ experiment.  It accepts
only an empty output directory and records the inputs and every stage artefact
under that directory, so it cannot overwrite a prior experimental result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from platforms.variable_mapping import build_platform_variable_map
from stage1.hazard_ranking import run_stage1_hazard_ranking
from stage1.semantic_extraction import run_stage1_extraction
from stage2.base_search import run_base_search, write_base_output
from stage2.conditional_search import run_conditional_search
from stage3.subregions import main as run_stage3
from simulators.wacsim_ctown.runtime_executor import create_executor


PLATFORM = "wacsim_ctown"
EXPECTED_MASKER = "T7_LEVEL_SP"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "wacsim_ctown_13_8_small_budget.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=460)
    parser.add_argument("--crave-commit", default="unknown")
    parser.add_argument("--crave-worktree-dirty", choices=("true", "false"), default="unknown")
    parser.add_argument("--docker-image", default="unknown")
    parser.add_argument("--docker-image-id", default="unknown")
    parser.add_argument("--invocation", default="")
    parser.add_argument("--runner-log", default="")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args(argv)


def _load_config(path: Path, seed: int) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if str(config.get("platform")) != PLATFORM:
        raise ValueError("smoke config platform must be wacsim_ctown")
    if int(config.get("seed", -1)) != int(seed):
        raise ValueError("CLI seed must equal the pre-recorded smoke config seed")
    stage2 = config.get("stage2", {})
    if not isinstance(stage2, Mapping) or list(stage2.get("directions", [])) != ["neg"]:
        raise ValueError("smoke config must use the reviewed negative driver direction only")
    if int(stage2.get("duration_stop", 0)) <= int(stage2.get("duration_start", 0)):
        raise ValueError("small-budget Stage 2 must contain more than one duration")
    if int(config.get("stage3", {}).get("representative_samples", 0)) < 2:
        raise ValueError("small-sample Stage 3 must contain at least two representative samples")
    if int(config.get("region_validation", {}).get("interior_sample_count", 0)) < 2:
        raise ValueError("region validation must contain at least two interior samples")
    return config


def _input_identity(root: Path, manifest_path: Path, config_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    execution = manifest["execution"]
    wacsim_root = root / str(execution["wacsim_submodule"])
    scenario = wacsim_root / str(execution["scenario"])
    lock = wacsim_root / str(execution["calibration_lock"])
    network = wacsim_root / str(execution["network"])
    implementation = wacsim_root / "wacsim" / "replay" / "engine.py"
    return {
        "recorded_at": _now(),
        "crave_commit": args.crave_commit,
        "crave_worktree_dirty": args.crave_worktree_dirty,
        "docker_image": {"reference": args.docker_image, "local_id": args.docker_image_id},
        "manifest": {"path": str(manifest_path.relative_to(root)), "sha256": _sha256(manifest_path)},
        "pipeline_config": {"path": str(config_path.relative_to(root)), "sha256": _sha256(config_path)},
        "crave_inputs": {
            "pipeline_script": {"path": str(Path(__file__).relative_to(root)), "sha256": _sha256(Path(__file__))},
            "controller": {"path": "simulators/wacsim_ctown/controller.py", "sha256": _sha256(root / "simulators/wacsim_ctown/controller.py")},
            "simulation": {"path": "simulators/wacsim_ctown/simulation.py", "sha256": _sha256(root / "simulators/wacsim_ctown/simulation.py")},
            "variable_mapping": {"path": "src/platforms/variable_mapping.py", "sha256": _sha256(root / "src/platforms/variable_mapping.py")},
            "requirements": {"path": "requirements.txt", "sha256": _sha256(root / "requirements.txt")},
        },
        "wacsim": {
            "submodule_path": str(execution["wacsim_submodule"]),
            "commit_expected": execution["wacsim_commit"],
            "base_image_digest_expected": execution["base_image_digest"],
            "scenario": {"path": str(execution["scenario"]), "expected_sha256": execution["scenario_sha256"], "actual_sha256": _sha256(scenario)},
            "calibration_lock": {"path": str(execution["calibration_lock"]), "expected_sha256": execution["calibration_lock_sha256"], "actual_sha256": _sha256(lock)},
            "network": {"path": str(execution["network"]), "actual_sha256": _sha256(network)},
            "implementation": {"path": "wacsim/replay/engine.py", "expected_sha256": execution["implementation_sha256"], "file_sha256": _sha256(implementation)},
        },
    }


def _replay_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    score = result.get("score", {})
    hazard_step = score.get("first_hazard_step")
    alarm_step = score.get("first_alarm_step")
    return {
        "status": result.get("status"),
        "run_key": result.get("run_key"),
        "rows": len(result.get("rows", [])),
        "first_hazard_step": hazard_step,
        "first_alarm_step": alarm_step,
        "prehazard_alarm_rule_ids": score.get("prehazard_alarm_rule_ids", []),
        "reach_avoid": hazard_step is not None and (alarm_step is None or alarm_step >= hazard_step),
        "fixed_identity": result.get("meta", {}).get("fixed_identity", {}),
        "error": result.get("error"),
    }


class _ProbeRecorder:
    """Persist every replay request and its compact common-result envelope."""

    def __init__(self, executor: Callable[[Mapping[str, Any]], dict[str, Any]], path: Path, stage: str):
        self._executor = executor
        self._path = path
        self._stage = stage
        self.count = 0
        self.ok_count = 0
        self.error_count = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._executor, name)

    def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        self.count += 1
        record: dict[str, Any] = {"ordinal": self.count, "stage": self._stage, "task": dict(task)}
        try:
            result = dict(self._executor(task))
            record["result"] = _replay_summary(result)
            if str(result.get("status", "ok")).lower() == "ok":
                self.ok_count += 1
            else:
                self.error_count += 1
            return result
        except Exception as exc:
            self.error_count += 1
            record["exception"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def stats(self) -> dict[str, int]:
        return {"probe_count": self.count, "ok_count": self.ok_count, "error_count": self.error_count}


def _interior_value(lower: float, upper: float, fraction: float) -> float:
    return lower if upper == lower else lower + (upper - lower) * fraction


def _region_validation(executor: Callable[[Mapping[str, Any]], dict[str, Any]], region: Mapping[str, Any], fraction: float) -> dict[str, Any]:
    duration_low, duration_high = (int(value) for value in region["driver_duration_range"])
    duration = int(round(_interior_value(float(duration_low), float(duration_high), fraction)))
    mask_low, mask_high = (float(value) for value in region["mask_slope_range"])
    mask_slope = _interior_value(mask_low, mask_high, fraction)
    driver_range = region.get("driver_amp_range", [region["driver_amp"], region["driver_amp"]])
    driver_amp = _interior_value(float(driver_range[0]), float(driver_range[1]), fraction)
    runtime = {"steps": duration + 100, "stop_on_trip": True}
    driver = {
        "point": str(region["hazard_driver"]),
        "amp": driver_amp,
        "duration": duration,
        "t_start": 0,
        "role": "manip",
    }
    masker = {
        "point": str(region["masking_variable"]),
        "amp": mask_slope,
        "duration": duration,
        "t_start": 0,
        "role": "mask",
    }
    baseline = executor({"runtime_args": runtime, "injection": {}})
    single = executor({"runtime_args": runtime, "injection": {"points": [driver]}})
    coordinated = executor({"runtime_args": runtime, "injection": {"points": [driver, masker]}})
    coordinated_summary = _replay_summary(coordinated)
    if not coordinated_summary["reach_avoid"]:
        raise RuntimeError("region-interior coordinated replay did not meet reach-avoid contract")
    return {
        "source_region_id": str(region["region_id"]),
        "interior_fraction": fraction,
        "duration": duration,
        "driver_slope": driver_amp,
        "mask_slope": mask_slope,
        "baseline": _replay_summary(baseline),
        "single_driver": _replay_summary(single),
        "coordinated": coordinated_summary,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"smoke output directory must be empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    simulator = root / "simulators" / PLATFORM
    manifest_path = simulator / "system_manifest.json"
    config_path = args.config.resolve()
    config = _load_config(config_path, args.seed)
    stage1_config = dict(config["stage1"])
    stage2_config = dict(config["stage2"])
    stage3_config = dict(config["stage3"])
    validation_config = dict(config["region_validation"])
    _write_json(output_root / "pipeline_config.json", config)
    _write_json(output_root / "input_identity.json", _input_identity(root, manifest_path, config_path, args))
    _write_json(output_root / "invocation.json", {"recorded_at": _now(), "command": args.invocation, "runner_log": args.runner_log, "argv": sys.argv})
    _write_json(output_root / "failure_records.json", {"records": []})
    current_stage = "stage1"
    try:
        variables = build_platform_variable_map(root, PLATFORM)
        extraction_paths = run_stage1_extraction(
            controller_path=simulator / "controller.py",
            simulation_path=simulator / "simulation.py",
            variable_registry=variables,
            manifest_path=manifest_path,
            output_root=output_root,
            platform=PLATFORM,
            seed=args.seed,
        )
        extraction_path = extraction_paths["artifact"]
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
        ranking_paths = run_stage1_hazard_ranking(
            root=root, platform=PLATFORM, extraction=extraction, output_root=output_root, seed=args.seed,
            duration=int(stage1_config["ranking_duration"]),
        )
        ranking = json.loads(ranking_paths["ranking_artifact"].read_text(encoding="utf-8"))
        hazard_id = str(stage2_config["hazard_id"])
        alarm_id = str(stage2_config["alarm_id"])
        driver = str(stage2_config["hazard_driver"])
        selected = ranking["rankings"][hazard_id][0]
        if str(selected["variable"]) != driver or str(selected["input_direction"]) != "decrease":
            raise RuntimeError(f"unexpected Stage 1 driver contract: {selected}")

        current_stage = "stage2_base"
        raw_dir = output_root / "results" / "raw_probes"
        base_recorder = _ProbeRecorder(
            create_executor(manifest_path), raw_dir / "stage2_base.jsonl", "stage2_base"
        )
        base = run_base_search(
            executor=base_recorder,
            hazard_driver=driver,
            hazard_id=hazard_id,
            alarm_id=alarm_id,
            duration_start=int(stage2_config["duration_start"]),
            duration_stop=int(stage2_config["duration_stop"]),
            duration_step=int(stage2_config["duration_step"]),
            duration_anchor_step=int(stage2_config["duration_anchor_step"]),
            amp_magnitude_range=tuple(float(value) for value in stage2_config["slope_magnitude_range"]),
            coarse_span=float(stage2_config["coarse_span"]),
            amp_tol=float(stage2_config["amp_tol"]),
            directions=tuple(str(value) for value in stage2_config["directions"]),
            workers=1,
            platform=PLATFORM,
        )
        base_path = output_root / "results" / "stage2" / PLATFORM / driver / "boundary_results.json"
        write_base_output(base_path, base)
        _write_json(
            output_root / "results" / "manifests" / "stage2_base" / PLATFORM / driver / "run_manifest.json",
            {"stage": "stage2_base", "platform": PLATFORM, "hazard_driver": driver, "seed": int(args.seed), "config": stage2_config, "raw_probe_records": str((raw_dir / "stage2_base.jsonl").relative_to(output_root)), "probe_stats": base_recorder.stats(), "duration_rule": "duration + 100"},
        )
        target_boundaries = [boundary for boundary in base.get("boundaries", []) if int(boundary.get("target_interval_count", 0)) > 0]
        if not target_boundaries:
            raise RuntimeError("Stage 2 base search returned no target boundary")

        current_stage = "stage2_conditional"
        conditional_path = output_root / "results" / "stage2" / PLATFORM / driver / "conditional_results.json"
        conditional_recorder = _ProbeRecorder(
            create_executor(manifest_path), raw_dir / "stage2_conditional.jsonl", "stage2_conditional"
        )
        conditional = run_conditional_search(
            boundary_path=base_path,
            extraction_path=extraction_path,
            manifest_path=manifest_path,
            output_path=conditional_path,
            output_root=output_root,
            platform=PLATFORM,
            direction="neg",
            amp_tol=float(stage2_config["amp_tol"]),
            max_bisect=int(stage2_config["conditional_max_bisect"]),
            seed=args.seed,
            workers=1,
            executor=conditional_recorder,
        )
        if not conditional.get("points"):
            raise RuntimeError("Stage 2 conditional search returned no coordinated point")

        current_stage = "stage3"
        stage3_recorder = _ProbeRecorder(
            create_executor(manifest_path), raw_dir / "stage3.jsonl", "stage3"
        )
        stage3 = run_stage3(
            [
                "--platform", PLATFORM,
                "--hazard-id", hazard_id,
                "--hazard-driver", driver,
                "--base-path", str(base_path),
                "--conditional-path", str(conditional_path),
                "--extraction-path", str(extraction_path),
                "--manifest-path", str(manifest_path),
                "--output-root", str(output_root),
                "--seed", str(args.seed),
                "--representative-samples", str(stage3_config["representative_samples"]),
                "--sample-duration-mod", str(stage3_config["sample_duration_mod"]),
                "--workers", "1",
            ],
            create_executor=lambda _path: stage3_recorder,
        )
        if not stage3.get("regions"):
            raise RuntimeError("Stage 3 returned no coordinated region")
        if any(str(region["masking_variable"]) != EXPECTED_MASKER for region in stage3["regions"]):
            raise RuntimeError("Stage 3 returned an unexpected masker")

        current_stage = "region_interior_validation"
        selected_regions = list(stage3["regions"])[: int(validation_config["max_regions"])]
        assignments = [selected_regions[index % len(selected_regions)] for index in range(int(validation_config["interior_sample_count"]))]
        totals = {str(region["region_id"]): sum(str(item["region_id"]) == str(region["region_id"]) for item in assignments) for region in selected_regions}
        seen: dict[str, int] = {}
        validation_recorder = _ProbeRecorder(
            create_executor(manifest_path), raw_dir / "region_validation.jsonl", "region_validation"
        )
        validation_samples: list[dict[str, Any]] = []
        for index, region in enumerate(assignments, start=1):
            region_id = str(region["region_id"])
            seen[region_id] = seen.get(region_id, 0) + 1
            fraction = seen[region_id] / (totals[region_id] + 1)
            validation_samples.append({"sample_id": f"S_{index:03d}", **_region_validation(validation_recorder, region, fraction)})
        _write_json(output_root / "region_interior_samples.json", {"samples": validation_samples, "probe_stats": validation_recorder.stats()})
        summary = {
            "status": "passed",
            "completed_at": _now(),
            "stage1": {"extraction": str(extraction_path.relative_to(output_root)), "ranking": str(ranking_paths["ranking_artifact"].relative_to(output_root)), "selected_driver": selected},
            "stage2": {"base": str(base_path.relative_to(output_root)), "boundary_count": len(base["boundaries"]), "target_boundary_count": len(target_boundaries), "conditional": str(conditional_path.relative_to(output_root)), "conditional_point_count": len(conditional["points"]), "base_probe_stats": base_recorder.stats(), "conditional_probe_stats": conditional_recorder.stats()},
            "stage3": {"region_count": len(stage3["regions"]), "region_ids": [region["region_id"] for region in stage3["regions"]], "probe_stats": stage3_recorder.stats()},
            "region_interior_validation": {"sample_count": len(validation_samples), "samples": validation_samples},
        }
        _write_json(output_root / "summary.json", summary)
        return summary
    except BaseException as exc:
        failure = {"status": "failed", "failed_at": _now(), "stage": current_stage, "exception_type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        _write_json(output_root / "failure_records.json", {"records": [failure]})
        _write_json(output_root / "summary.json", failure)
        raise


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
