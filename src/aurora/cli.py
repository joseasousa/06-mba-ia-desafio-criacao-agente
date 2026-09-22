from __future__ import annotations

import argparse
from pathlib import Path

from .config import get_settings
from .domain import DomainRepository


def _remove_sqlite(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def reset() -> None:
    parser = argparse.ArgumentParser(description="Restaura os dados iniciais do Residencial Aurora")
    parser.add_argument(
        "--all",
        action="store_true",
        help="também apaga sessões e eventos persistidos do ADK",
    )
    args = parser.parse_args()
    settings = get_settings()
    repository = DomainRepository(settings.aurora_domain_db, settings.data_dir)
    repository.restore(clear_confirmations=args.all)
    if args.all:
        _remove_sqlite(settings.aurora_session_db)
    print("Dados iniciais restaurados" + ("; sessões removidas" if args.all else "; sessões preservadas"))
