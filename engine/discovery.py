from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from engine.algorithms import StatusAlgorithm


@dataclass(frozen=True)
class DiscoveredAlgorithm:
    algorithm: StatusAlgorithm
    device_id: Optional[int]


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"algospec_{path.parent.name}_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def load_algorithm_specs(root: Path) -> list[StatusAlgorithm]:
    specs: list[StatusAlgorithm] = []
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
            algo = getattr(module, "ALGORITHM", None)
            if isinstance(algo, StatusAlgorithm):
                specs.append(algo)
        except Exception as e:
            print(f"[status-engine] failed to load algorithm {path}: {e}", file=sys.stderr)
    return specs


def discover(
    root: Path, resolver: Callable[[str, str], Optional[int]]
) -> list[DiscoveredAlgorithm]:
    return [
        DiscoveredAlgorithm(algorithm=a, device_id=resolver(a.company, a.device_key))
        for a in load_algorithm_specs(root)
    ]
