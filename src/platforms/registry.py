"""One authoritative registry for CRAVE-supported simulation platforms.

Platform-specific values belong here (or, for WaCSim CTown, in its reviewed
JSON case file).  Callers resolve an ID or alias through this module instead
of adding distributed ``if platform == ...`` branches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PlatformSpec:
    platform_id: str
    aliases: tuple[str, ...]
    display_name: str
    stage1_dir: str
    short_name: str
    manifest_relpath: str
    runtime_executor_module: str
    rq1_default: bool
    rq1: Mapping[str, Any]
    rq2: Mapping[str, Any]
    rq4: Mapping[str, Any]


_ROOT = Path(__file__).resolve().parents[2]
_WACSIM_CASE_PATH = _ROOT / "evaluation" / "configs" / "platforms" / "wacsim_ctown.json"
_DEFAULT_RQ2_PLATFORM = "tennessee_eastman"
_DEFAULT_RQ4_BOUNDARY_PLATFORM = "tennessee_eastman"
_DEFAULT_RQ4_PARTITION_PLATFORM = "boiler_ccs"


def _wacsim_case() -> dict[str, Any]:
    data = json.loads(_WACSIM_CASE_PATH.read_text(encoding="utf-8"))
    if data.get("platform_id") != "wacsim_ctown":
        raise ValueError(f"invalid WaCSim platform case: {_WACSIM_CASE_PATH}")
    return data


def _build_specs() -> dict[str, PlatformSpec]:
    wacsim = _wacsim_case()
    specs = (
        PlatformSpec(
            platform_id="boiler_ccs",
            aliases=("boiler", "boilerccs"),
            display_name="Boiler CCS",
            stage1_dir="boilerCCS",
            short_name="boiler",
            manifest_relpath="simulators/boiler_ccs/system_manifest.json",
            runtime_executor_module="simulators.boiler_ccs.runtime_executor",
            rq1_default=True,
            rq1={},
            rq2={},
            rq4={"partition_drivers": ("fuel_command", "water_pump_speed")},
        ),
        PlatformSpec(
            platform_id="tennessee_eastman",
            aliases=("te",),
            display_name="TE",
            stage1_dir="TE",
            short_name="te",
            manifest_relpath="simulators/tennessee_eastman/system_manifest.json",
            runtime_executor_module="simulators.tennessee_eastman.runtime_executor",
            rq1_default=True,
            rq1={},
            rq2={
                "driver": "xmv_07",
                "duration_range": (100, 1000),
                "duration_step": 5,
                "rate_range": (0.0, 1.0),
                "hazard_id": "H-TEP-SEPARATOR-LEVEL-LOW",
                "alarm_id": "A-TEP-SEP-LEVEL-TRACK",
            },
            rq4={
                "boundary_driver": "xmv_07",
                "boundary_reference": {"setting_id": "reference", "delta_T": 2, "epsilon_K": 1e-5},
            },
        ),
        PlatformSpec(
            platform_id=str(wacsim["platform_id"]),
            aliases=tuple(str(value) for value in wacsim["aliases"]),
            display_name=str(wacsim["display_name"]),
            stage1_dir=str(wacsim["stage1_dir"]),
            short_name=str(wacsim["short_name"]),
            manifest_relpath=str(wacsim["manifest_relpath"]),
            runtime_executor_module=str(wacsim["runtime_executor_module"]),
            rq1_default=bool(wacsim.get("rq1_default", False)),
            rq1=dict(wacsim.get("rq1", {})),
            rq2=dict(wacsim["rq2"]),
            rq4=dict(wacsim["rq4"]),
        ),
    )
    return {spec.platform_id: spec for spec in specs}


def all_platforms() -> dict[str, PlatformSpec]:
    """Return registered platforms keyed by their canonical ID."""
    return _build_specs()


def normalize_platform(platform: str) -> str:
    value = str(platform).strip().lower()
    for platform_id, spec in all_platforms().items():
        if value == platform_id or value in {alias.lower() for alias in spec.aliases}:
            return platform_id
    raise ValueError(f"unsupported platform: {platform}")


def get_platform(platform: str) -> PlatformSpec:
    return all_platforms()[normalize_platform(platform)]


def manifest_path(root: Path, platform: str) -> Path:
    return Path(root) / get_platform(platform).manifest_relpath


def rq1_default_platforms() -> tuple[str, ...]:
    return tuple(spec.platform_id for spec in all_platforms().values() if spec.rq1_default)


def rq1_cases() -> dict[str, dict[str, str]]:
    return {
        spec.platform_id: {
            "display_name": spec.display_name,
            "stage1_dir": spec.stage1_dir,
            "short_name": spec.short_name,
            "manifest_platform": spec.platform_id,
        }
        for spec in all_platforms().values()
    }


def default_rq2_platform() -> PlatformSpec:
    return get_platform(_DEFAULT_RQ2_PLATFORM)


def default_rq4_boundary_platform() -> PlatformSpec:
    return get_platform(_DEFAULT_RQ4_BOUNDARY_PLATFORM)


def default_rq4_partition_platform() -> PlatformSpec:
    return get_platform(_DEFAULT_RQ4_PARTITION_PLATFORM)
