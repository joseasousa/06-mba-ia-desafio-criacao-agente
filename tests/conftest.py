from __future__ import annotations

from pathlib import Path

import pytest

from aurora.domain import DomainRepository


@pytest.fixture
def repository(tmp_path: Path) -> DomainRepository:
    data_dir = Path(__file__).resolve().parents[1] / "dados"
    repo = DomainRepository(tmp_path / "domain.db", data_dir)
    repo.initialize()
    return repo

