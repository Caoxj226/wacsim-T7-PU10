"""Translate a CRAVE task into the fixed WaCSim CTown replay contract.

This adapter must run in the Linux Docker image.  It uses only repository
relative paths by default and never imports WaCSim at module-import time, so
Windows can validate CRAVE's source tree without trying to load WaCSim's Linux
EPANET extension.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import queue
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SYSTEM_ID = "wacsim_ctown"
WARMUP_STEPS = 24
POST_ATTACK_OBSERVATION_STEPS = 100
SLOPE_RANGE = (-0.05, -0.001)
DEFAULT_REPLAY_TIMEOUT_SECONDS = 120.0
MAX_REPLAY_TIMEOUT_SECONDS = 600.0


def _manifest(manifest_path: Path) -> dict[str, Any]:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixed_identity(manifest_path: Path, root: Path | None = None) -> dict[str, Any]:
    """Describe the fixed evidence that identifies every replay attempt."""
    manifest = _manifest(manifest_path)
    execution = manifest.get("execution", {})
    identity = {
        "system_id": SYSTEM_ID,
        "wacsim_commit": execution.get("wacsim_commit"),
        "scenario_expected_sha256": execution.get("scenario_sha256"),
        "calibration_lock_expected_sha256": execution.get("calibration_lock_sha256"),
        "implementation_expected_sha256": execution.get("implementation_sha256"),
    }
    if root is not None:
        scenario = root / str(execution["scenario"])
        lock = root / str(execution["calibration_lock"])
        identity.update(
            {
                "wacsim_root": str(root),
                "scenario_actual_sha256": _sha256(scenario),
                "calibration_lock_actual_sha256": _sha256(lock),
            }
        )
    return identity


def _repository_root(manifest_path: Path) -> Path:
    # CRAVE/simulators/wacsim_ctown/system_manifest.json -> CRAVE
    return Path(manifest_path).resolve().parents[2]


def resolve_wacsim_root(manifest_path: Path) -> Path:
    """Return the pinned source tree without embedding a host-specific path."""
    configured = os.environ.get("WACSIM_ROOT")
    root = Path(configured).resolve() if configured else _repository_root(manifest_path) / "third_party" / "WaCSim"
    if not (root / "wacsim" / "replay" / "engine.py").is_file():
        raise RuntimeError(
            "fixed WaCSim source is unavailable; initialise third_party/WaCSim "
            "or set WACSIM_ROOT inside the Docker integration image"
        )
    return root


def _load_wacsim(manifest_path: Path):
    if not sys.platform.startswith("linux"):
        raise RuntimeError(
            "WaCSim CTown replay requires the Linux Docker integration image; "
            "Windows may perform only static source checks."
        )
    root = resolve_wacsim_root(manifest_path)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    import wacsim  # pylint: disable=import-outside-toplevel
    imported_root = Path(wacsim.__file__).resolve().parent.parent
    if imported_root != root.resolve():
        raise RuntimeError(
            f"WaCSim import identity mismatch: expected {root.resolve()}, got {imported_root}"
        )
    from wacsim.replay.contract import InjectionSpec  # pylint: disable=import-outside-toplevel
    from wacsim.replay.engine import run_epanet  # pylint: disable=import-outside-toplevel
    from wacsim.replay.scenario import RunConfig, create_task, implementation_sha  # pylint: disable=import-outside-toplevel
    import yaml  # pylint: disable=import-outside-toplevel
    identity = _fixed_identity(manifest_path, root)
    if identity["scenario_actual_sha256"] != identity["scenario_expected_sha256"]:
        raise RuntimeError("fixed WaCSim scenario bytes do not match the reviewed identity")
    if identity["calibration_lock_actual_sha256"] != identity["calibration_lock_expected_sha256"]:
        raise RuntimeError("fixed WaCSim calibration-lock bytes do not match the reviewed identity")
    actual_implementation = implementation_sha()
    if actual_implementation != identity["implementation_expected_sha256"]:
        raise RuntimeError("fixed WaCSim implementation bytes do not match the reviewed identity")
    identity["implementation_actual_sha256"] = actual_implementation
    return root, InjectionSpec, RunConfig, create_task, run_epanet, yaml, identity


def load_injection_registry(manifest_path: Path) -> dict[str, dict[str, Any]]:
    """Expose the two reviewed writable points to CRAVE."""
    registry: dict[str, dict[str, Any]] = {}
    for point in _manifest(manifest_path)["injection_points"]:
        registry[str(point["name"])] = {
            "phase": str(point["phase"]),
            "field": str(point["field"]),
            "trace_field": str(point["field"]),
            "default_value": float(point["default_value"]),
            "range": list(point["range"]),
            "slope_range_per_step": list(point["slope_range_per_step"]),
            "slope_magnitude_range": list(point["slope_magnitude_range"]),
            "rate": float(point["rate"]),
            "default_direction": str(point.get("default_direction", "pos")),
            "role": str(point["role"]),
        }
    return registry


def load_rules_from_manifest(manifest_path: Path) -> dict[str, Any]:
    data = _manifest(manifest_path)
    return {
        "hazards": {rule["id"]: dict(rule) for rule in data["hazard_rules"]},
        "alarms": {rule["id"]: dict(rule) for rule in data["alarm_rules"]},
    }


def normalize_injection_points(injection: Mapping[str, Any]) -> list[dict[str, Any]]:
    points = injection.get("points")
    if isinstance(points, list):
        normalized: list[dict[str, Any]] = []
        for index, point in enumerate(points):
            if not isinstance(point, Mapping):
                raise ValueError(f"CTown injection point {index} must be an object")
            value = str(point.get("point", "")).strip()
            if not value:
                raise ValueError(f"CTown injection point {index} is missing point")
            normalized.append(dict(point, point=value))
        return normalized
    point = str(injection.get("point", "")).strip()
    return [] if not point else [dict(injection, point=point)]


def _failure(task: Mapping[str, Any], exc: Exception, *, manifest_path: Path | None = None) -> dict[str, Any]:
    identity = _fixed_identity(manifest_path) if manifest_path is not None else {"system_id": SYSTEM_ID}
    payload = json.dumps(
        {"task": task, "fixed_identity": identity},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    remote_failure_class = getattr(exc, "failure_class", None)
    if isinstance(exc, TimeoutError):
        failure_class = "timeout"
    elif isinstance(remote_failure_class, str):
        failure_class = remote_failure_class
    elif isinstance(exc, (ValueError, TypeError)):
        failure_class = "invalid_task"
    elif isinstance(exc, RuntimeError):
        failure_class = "runtime"
    else:
        failure_class = "exception"
    return {
        "status": "error",
        "rows": [],
        "score": {},
        "injection_echo": [],
        "meta": {
            "backend": "WaCSim-CTown",
            "failure_class": failure_class,
            "exception_type": type(exc).__name__,
            "fixed_identity": identity,
        },
        "run_key": "rk_" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "error": f"{type(exc).__name__}: {exc}",
    }


def _build_specs(
    raw_points: Sequence[Mapping[str, Any]], registry: Mapping[str, Mapping[str, Any]], InjectionSpec: Any
) -> tuple[Any, ...]:
    specs: list[Any] = []
    for raw in raw_points:
        point = str(raw.get("point", "")).strip()
        if point not in registry:
            raise ValueError(f"unknown CTown injection point: {point}")
        declared = registry[point]
        raw_role = str(raw.get("role", "")).strip()
        # Existing CRAVE scans use the generic role ``manip``.  At the
        # reviewed CTown boundary, its meaning is fixed by the point itself.
        role = declared["role"] if raw_role in {"", "manip"} else raw_role
        # CRAVE calls the second point in a cooperative search ``mask``;
        # WaCSim's reviewed replay contract calls that same role ``masker``.
        if raw_role == "mask" and declared["role"] == "masker":
            role = "masker"
        if role != declared["role"]:
            raise ValueError(f"role mismatch for {point}: expected {declared['role']}")
        slope = _finite_float("slope", raw.get("slope", raw.get("amp", raw.get("delta", 0.0))))
        if not SLOPE_RANGE[0] <= slope <= SLOPE_RANGE[1]:
            raise ValueError(f"CTown slope for {point} must be in {SLOPE_RANGE}, got {slope}")
        duration = _strict_int("duration", raw.get("duration", 0))
        if not 5 <= duration <= 120:
            raise ValueError(f"CTown duration must be in [5, 120], got {duration}")
        if "start_step" in raw:
            raise ValueError("CTown accepts score-relative t_start, not an absolute start_step")
        requested_start = _strict_int("t_start", raw.get("t_start", 0))
        # CRAVE's public task time is relative to its scored window.  The
        # reviewed scenario fixes the only admissible attack start at its
        # first scored transition, so only relative zero is valid.
        if requested_start != 0:
            raise ValueError("CTown only supports score-relative t_start=0 (hydraulic step 24)")
        if "range" in raw and not _same_range(raw["range"], declared["range"]):
            raise ValueError(f"CTown range for {point} differs from the reviewed engineering range")
        start_step = WARMUP_STEPS
        specs.append(
            InjectionSpec(
                point=point, role=role, slope=slope, duration=duration,
                start_step=start_step, range=tuple(declared["range"]),
            )
        )
    if len({spec.point for spec in specs}) != len(specs):
        raise ValueError("CTown does not allow duplicate injection points")
    roles = {spec.role for spec in specs}
    if roles not in (set(), {"driver"}, {"driver", "masker"}):
        raise ValueError("CTown accepts no attack, a driver, or a driver plus masker")
    if len(specs) > 1 and roles != {"driver", "masker"}:
        raise ValueError("CTown two-point attacks require exactly one driver and one masker")
    return tuple(specs)


def _finite_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"CTown {name} must be a finite number")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CTown {name} must be a finite number") from exc
    if not math.isfinite(converted):
        raise ValueError(f"CTown {name} must be a finite number")
    return converted


def _strict_int(name: str, value: Any) -> int:
    converted = _finite_float(name, value)
    if not converted.is_integer():
        raise ValueError(f"CTown {name} must be an integer, got {value!r}")
    return int(converted)


def _same_range(value: Any, expected: Sequence[Any]) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return False
    try:
        return all(_finite_float("range", actual) == float(target) for actual, target in zip(value, expected))
    except ValueError:
        return False


def _expected_steps(specs: Sequence[Any], runtime_args: Mapping[str, Any]) -> int:
    if specs:
        pairs = {(spec.start_step, spec.duration) for spec in specs}
        if len(pairs) != 1:
            raise ValueError("driver and masker must share start step and duration")
        start_step, duration = pairs.pop()
        expected = start_step + duration + POST_ATTACK_OBSERVATION_STEPS
        requested = runtime_args.get("steps")
        if requested is not None and _strict_int("runtime_args.steps", requested) not in {expected, expected - WARMUP_STEPS}:
            raise ValueError(f"CTown requires {expected} total steps including warm-up for this attack")
        return expected
    total_steps = _strict_int("runtime_args.steps", runtime_args.get("steps", WARMUP_STEPS + POST_ATTACK_OBSERVATION_STEPS))
    if total_steps < WARMUP_STEPS:
        raise ValueError(f"CTown no-attack replay requires at least {WARMUP_STEPS} total steps")
    return total_steps


class _ReplayWorkerError(RuntimeError):
    """A replay failure transported from the isolated worker process."""

    def __init__(self, exception_type: str, message: str, failure_class: str) -> None:
        super().__init__(f"{exception_type}: {message}")
        self.failure_class = failure_class


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_task"
    if isinstance(exc, RuntimeError):
        return "runtime"
    return "exception"


def _timeout_seconds(task: Mapping[str, Any], manifest_path: Path) -> float:
    runtime_args = task.get("runtime_args", {})
    if not isinstance(runtime_args, Mapping):
        raise ValueError("runtime_args must be an object")
    execution = _manifest(manifest_path).get("execution", {})
    value = runtime_args.get("timeout_seconds", execution.get("replay_timeout_seconds", DEFAULT_REPLAY_TIMEOUT_SECONDS))
    timeout = _finite_float("runtime_args.timeout_seconds", value)
    if not 1.0 <= timeout <= MAX_REPLAY_TIMEOUT_SECONDS:
        raise ValueError(
            f"CTown timeout_seconds must be within [1, {MAX_REPLAY_TIMEOUT_SECONDS:g}], got {timeout}"
        )
    return timeout


def _simulate_once(
    task: Mapping[str, Any], *, registry: Mapping[str, Mapping[str, Any]], manifest_path: Path
) -> dict[str, Any]:
    """Run one replay in the current process; callers provide isolation."""
    root, InjectionSpec, RunConfig, create_task, run_epanet, yaml, identity = _load_wacsim(manifest_path)
    raw = normalize_injection_points(task.get("injection", {}))
    specs = _build_specs(raw, registry, InjectionSpec)
    runtime_args = task.get("runtime_args", {})
    if not isinstance(runtime_args, Mapping):
        raise ValueError("runtime_args must be an object")
    total_steps = _expected_steps(specs, runtime_args)
    scenario = root / "examples" / "crave_ctown" / "scenario.yaml"
    lock = root / "examples" / "crave_ctown" / "calibration.lock.yaml"
    network = root / "examples" / "ctown_topology" / "ctown_map.inp"
    locked = yaml.safe_load(lock.read_text(encoding="utf-8"))
    config = RunConfig(
        locked["nominal_setpoint_m"], locked["Kp"], locked["Ki_per_step"], locked["theta_m"]
    )
    replay_task = create_task(network, scenario, config, total_steps, specs, lock)
    result = run_epanet(replay_task, config, network, scenario)
    output = result.to_dict()
    output["meta"] = {
        **output["meta"],
        "adapter": SYSTEM_ID,
        "execution_mode": "fast_epanet_replay",
        "wacsim_root": "third_party/WaCSim",
        "fixed_identity": identity,
    }
    return output


def _replay_worker(
    task: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]], manifest_path: str, result_queue: Any
) -> None:
    try:
        result_queue.put(("ok", _simulate_once(task, registry=registry, manifest_path=Path(manifest_path))))
    except BaseException as exc:  # the parent turns this into the common error envelope
        result_queue.put(("error", type(exc).__name__, str(exc), _failure_class(exc)))


def _terminate_process(process: multiprocessing.Process) -> None:
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def _run_with_timeout(
    task: Mapping[str, Any], *, registry: Mapping[str, Mapping[str, Any]], manifest_path: Path, timeout_seconds: float
) -> dict[str, Any]:
    """Execute replay in a child and forcibly reap it if the deadline expires."""
    if not sys.platform.startswith("linux"):
        # Preserve the explicit platform guard without creating Windows child
        # processes merely to report that the required Docker runtime is absent.
        return _simulate_once(task, registry=registry, manifest_path=manifest_path)
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_replay_worker,
        args=(dict(task), dict(registry), str(manifest_path), result_queue),
    )
    process.start()
    try:
        try:
            message = result_queue.get(timeout=float(timeout_seconds))
        except queue.Empty as exc:
            _terminate_process(process)
            raise TimeoutError(f"WaCSim replay exceeded {timeout_seconds:g} seconds and was terminated") from exc
        if not isinstance(message, tuple) or not message:
            raise RuntimeError("WaCSim replay worker returned an invalid result")
        if message[0] == "ok":
            return dict(message[1])
        if message[0] == "error" and len(message) == 4:
            raise _ReplayWorkerError(str(message[1]), str(message[2]), str(message[3]))
        raise RuntimeError("WaCSim replay worker returned an unrecognised result")
    finally:
        if process.is_alive():
            _terminate_process(process)
        else:
            process.join(timeout=1)
        result_queue.close()
        result_queue.join_thread()


def simulate(
    task: Mapping[str, Any], *, registry: Mapping[str, Mapping[str, Any]], manifest_path: Path
) -> dict[str, Any]:
    """Run one formal fast replay and return CRAVE's common result envelope."""
    try:
        timeout_seconds = _timeout_seconds(task, Path(manifest_path))
        return _run_with_timeout(
            task, registry=registry, manifest_path=Path(manifest_path), timeout_seconds=timeout_seconds
        )
    except Exception as exc:  # executor failures are data, not unhandled worker crashes
        return _failure(task, exc, manifest_path=Path(manifest_path))


def create_executor(manifest_path: Path):
    registry = load_injection_registry(manifest_path)

    def bound_simulate(task: Mapping[str, Any]) -> dict[str, Any]:
        return simulate(task, registry=registry, manifest_path=Path(manifest_path))

    bound_simulate.registry = registry
    bound_simulate.rules = load_rules_from_manifest(manifest_path)
    bound_simulate.system_id = SYSTEM_ID
    bound_simulate.manifest_path = Path(manifest_path)
    bound_simulate.warmup_steps = WARMUP_STEPS
    bound_simulate.score_start_step = WARMUP_STEPS
    return bound_simulate
