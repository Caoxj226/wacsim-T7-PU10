from __future__ import annotations

try:  # supports both ``PYTHONPATH=src`` and ``import src.stage1...`` callers
    from platforms.registry import get_platform
except ModuleNotFoundError:  # pragma: no cover - depends on the entrypoint style
    from src.platforms.registry import get_platform


def stage1_artifact_dir(platform: str) -> str:
    return get_platform(platform).stage1_dir
