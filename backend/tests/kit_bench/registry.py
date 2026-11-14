"""The constructed protocol fixtures, in order."""

from __future__ import annotations

import importlib
from types import ModuleType

NAMES = [
    "syn01_parallel_table",
    "syn02_oncology_cycles",
    "syn03_vaccine_prose",
    "syn04_crossover_borderless",
    "syn05_long_multipart",
    "syn06_beyond_vocabulary",
]


def fixtures() -> list[ModuleType]:
    return [importlib.import_module(f"{__package__}.fixtures.{name}") for name in NAMES]
