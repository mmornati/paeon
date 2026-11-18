from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from paeon.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "paeon.sqlite")
    yield s
    s.close()
