from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


_AUTO_BASETEMP_ATTRIBUTE = "_ai_autofigure_owned_basetemp"


def pytest_configure(config: pytest.Config) -> None:
    """Give each invocation an isolated OS-temp root unless the caller chose one."""
    if config.option.basetemp is not None:
        return
    owned = Path(tempfile.mkdtemp(prefix="ai-autofigure-pytest-"))
    config.option.basetemp = str(owned)
    setattr(config, _AUTO_BASETEMP_ATTRIBUTE, owned)


def pytest_unconfigure(config: pytest.Config) -> None:
    owned = getattr(config, _AUTO_BASETEMP_ATTRIBUTE, None)
    if owned is not None:
        shutil.rmtree(owned, ignore_errors=True)
