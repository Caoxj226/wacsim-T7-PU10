"""Run one frozen WaCSim RQ only after experiment-identity preflight.

This is deliberately a WaCSim-only host-side launcher.  It does not change
the shared RQ modules or the Boiler/TE invocation model.  The launcher checks
the experiment manifest, then starts the manifest's Docker command verbatim
except for substituting the verified output-root mount and immutable image
digest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


RQ_NAMES = ("rq1", "rq2", "rq3", "rq4")


class PreflightError(RuntimeError):
    """Raised when a frozen experiment identity no longer matches disk."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_text(argv: Sequence[str], *, cwd: Path) -> str:
    return subprocess.run(list(argv), cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def _git_head(path: Path) -> str:
    return _run_text(["git", "-C", str(path), "rev-parse", "HEAD"], cwd=path)


def _image_identity(reference: str, *, cwd: Path) -> tuple[str, set[str]]:
    template = "{{.Id}}\n{{join .RepoDigests \"\\n\"}}"
    output = _run_text(["docker", "image", "inspect", reference, "--format", template], cwd=cwd)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise PreflightError(f"Docker image inspect returned no identity for {reference}")
    return lines[0], set(lines[1:])


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read experiment manifest: {path}") from exc
    if data.get("schema_version") != 1:
        raise PreflightError("unsupported experiment-manifest schema")
    if data.get("status") != "frozen_not_executed":
        raise PreflightError("experiment manifest is not an unexecuted frozen experiment")
    return data


def _checked_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise PreflightError(f"frozen input escapes repository root: {relative}")
    if not path.is_file():
        raise PreflightError(f"frozen input is missing: {relative}")
    return path


def _verify_inputs(root: Path, frozen_inputs: Mapping[str, Any]) -> list[dict[str, str]]:
    checked: list[dict[str, str]] = []
    for name in ("scenario", "calibration_lock", "system_manifest", "variable_table", "platform_case"):
        entry = frozen_inputs.get(name)
        if not isinstance(entry, Mapping):
            raise PreflightError(f"manifest lacks frozen input: {name}")
        path = _checked_path(root, str(entry.get("path", "")))
        actual = _sha256(path)
        expected = str(entry.get("sha256", ""))
        if actual != expected:
            raise PreflightError(f"hash mismatch for {name}: expected {expected}, got {actual}")
        checked.append({"name": name, "path": str(path), "sha256": actual})

    implementation = frozen_inputs.get("implementation")
    if not isinstance(implementation, Mapping):
        raise PreflightError("manifest lacks frozen implementation identity")
    implementation_path = _checked_path(root, str(implementation.get("path", "")))
    actual_file_hash = _sha256(implementation_path)
    expected_file_hash = str(implementation.get("file_sha256", ""))
    if actual_file_hash != expected_file_hash:
        raise PreflightError(
            f"implementation file hash mismatch: expected {expected_file_hash}, got {actual_file_hash}"
        )
    checked.append({"name": "implementation", "path": str(implementation_path), "sha256": actual_file_hash})
    return checked


def _output_root(manifest_path: Path, manifest: Mapping[str, Any]) -> Path:
    output = manifest.get("output_root")
    if not isinstance(output, Mapping):
        raise PreflightError("manifest lacks output_root")
    declared = Path(str(output.get("host", ""))).resolve()
    actual = manifest_path.parent.resolve()
    if declared != actual:
        raise PreflightError(f"manifest must reside in its declared output root: {declared}")
    return actual


def _materialize_command(manifest: Mapping[str, Any], rq: str, output_root: Path) -> list[str]:
    command_spec = manifest.get("rq_commands", {}).get(rq)
    if not isinstance(command_spec, Mapping) or command_spec.get("status") != "frozen_not_executed":
        raise PreflightError(f"manifest lacks a frozen command for {rq}")
    source = command_spec.get("argv")
    if not isinstance(source, list) or not all(isinstance(item, str) for item in source):
        raise PreflightError(f"manifest command for {rq} is malformed")
    image = manifest.get("source_identity", {}).get("docker_image", {})
    if not isinstance(image, Mapping):
        raise PreflightError("manifest lacks Docker image identity")
    tag = str(image.get("reference", ""))
    digest = str(image.get("repo_digest", ""))
    if not tag or not digest:
        raise PreflightError("manifest Docker image identity is incomplete")
    mount = f"type=bind,source={output_root},target=/opt/crave/results"
    command: list[str] = []
    for item in source:
        if item == "<mount>":
            command.extend(["--mount", mount])
        elif item == tag:
            command.append(digest)
        else:
            command.append(item)
    if "<mount>" not in source or digest not in command:
        raise PreflightError(f"manifest command for {rq} does not use the frozen mount and image")
    return command


def preflight(manifest_path: Path, rq: str, *, root: Path | None = None) -> dict[str, Any]:
    root = (root or _repo_root()).resolve()
    manifest = _load_manifest(manifest_path)
    output_root = _output_root(manifest_path, manifest)
    source = manifest.get("source_identity", {})
    if not isinstance(source, Mapping):
        raise PreflightError("manifest lacks source_identity")
    expected_crave = str(source.get("crave_commit", ""))
    actual_crave = _git_head(root)
    if actual_crave != expected_crave:
        raise PreflightError(f"CRAVE commit mismatch: expected {expected_crave}, got {actual_crave}")
    expected_wacsim = str(source.get("wacsim_commit", ""))
    submodule = root / str(source.get("wacsim_submodule_path", "third_party/WaCSim"))
    actual_wacsim = _git_head(submodule)
    if actual_wacsim != expected_wacsim:
        raise PreflightError(f"WaCSim commit mismatch: expected {expected_wacsim}, got {actual_wacsim}")

    image = source.get("docker_image", {})
    if not isinstance(image, Mapping):
        raise PreflightError("manifest lacks Docker image identity")
    image_id, repo_digests = _image_identity(str(image.get("reference", "")), cwd=root)
    expected_image_id = str(image.get("local_image_id", ""))
    expected_digest = str(image.get("repo_digest", ""))
    if image_id != expected_image_id or expected_digest not in repo_digests:
        raise PreflightError("Docker image identity does not match the frozen manifest")

    command = _materialize_command(manifest, rq, output_root)
    return {
        "status": "passed",
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "experiment_id": manifest["experiment_id"],
        "rq": rq,
        "manifest_path": str(manifest_path.resolve()),
        "crave_commit": actual_crave,
        "wacsim_commit": actual_wacsim,
        "docker_image_id": image_id,
        "docker_repo_digest": expected_digest,
        "verified_inputs": _verify_inputs(root, manifest.get("frozen_inputs", {})),
        "command": command,
    }


def _write_preflight(output_root: Path, rq: str, payload: Mapping[str, Any]) -> Path:
    directory = output_root / "preflight"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{rq}_preflight.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--rq", required=True, choices=RQ_NAMES)
    parser.add_argument("--verify-only", action="store_true", help="verify and write preflight, without Docker execution")
    parser.add_argument("--dry-run", action="store_true", help="verify and print the frozen Docker command, without execution")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = preflight(args.manifest, args.rq)
        output_root = args.manifest.resolve().parent
        preflight_path = _write_preflight(output_root, args.rq, payload)
        print(f"preflight passed: {preflight_path}")
        print("command:", " ".join(payload["command"]))
        if args.verify_only or args.dry_run:
            return 0
        return subprocess.run(payload["command"], cwd=_repo_root(), check=False).returncode
    except (OSError, subprocess.CalledProcessError, PreflightError) as exc:
        print(f"frozen WaCSim RQ launch refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
