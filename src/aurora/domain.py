from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


class DomainError(Exception):
    pass


class ApartmentNotFound(DomainError):
    pass


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS apartamentos (
    numero TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS areas (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL CHECK (taxa >= 0)
);
CREATE TABLE IF NOT EXISTS reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    area TEXT NOT NULL REFERENCES areas(id),
    data TEXT NOT NULL,
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    idempotency_key TEXT UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
ON reservas(area, data) WHERE ativa = 1;
CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    nome TEXT NOT NULL,
    data TEXT NOT NULL,
    idempotency_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS confirmacoes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    function_call_id TEXT NOT NULL,
    acao TEXT NOT NULL,
    detalhes_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'approved', 'denied')),
    UNIQUE(session_id, function_call_id)
);
CREATE INDEX IF NOT EXISTS ix_confirmacao_session_status
ON confirmacoes(session_id, status);
"""


class DomainRepository:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(SCHEMA)
        with self.connect(immediate=True) as connection:
            initialized = connection.execute(
                "SELECT 1 FROM metadata WHERE key = 'initialized'"
            ).fetchone()
            if not initialized:
                self._load_reference_data(connection)
                self._restore_mutable_data(connection)
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('initialized', '1')"
                )

    def restore(self, *, clear_confirmations: bool = False) -> None:
        self.initialize()
        with self.connect(immediate=True) as connection:
            if clear_confirmations:
                connection.execute("DELETE FROM confirmacoes")
            connection.execute("DELETE FROM visitantes")
            connection.execute("DELETE FROM reservas")
            self._restore_mutable_data(connection)

    def _json(self, name: str) -> list[dict[str, Any]]:
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    def _load_reference_data(self, connection: sqlite3.Connection) -> None:
        connection.executemany(
            "INSERT INTO apartamentos(numero, morador) VALUES (:numero, :morador)",
            self._json("apartamentos.json"),
        )
        connection.executemany(
            "INSERT INTO areas(id, nome, taxa) VALUES (:id, :nome, :taxa)",
            self._json("areas.json"),
        )

    def _restore_mutable_data(self, connection: sqlite3.Connection) -> None:
        connection.executemany(
            "INSERT INTO reservas(codigo, apartamento, area, data) "
            "VALUES (:codigo, :apartamento, :area, :data)",
            self._json("reservas.json"),
        )
        connection.executemany(
            "INSERT INTO visitantes(apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
            self._json("visitantes.json"),
        )

    @staticmethod
    def _validate_date(value: str) -> str:
        date.fromisoformat(value)
        return value

    def apartment_exists(self, apartamento: str) -> bool:
        self.initialize()
        with self.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM apartamentos WHERE numero = ?", (apartamento,)
            ).fetchone() is not None

    def require_apartment(self, apartamento: str) -> None:
        if not self.apartment_exists(apartamento):
            raise ApartmentNotFound(apartamento)

    def get_area(self, area_id: str) -> Area | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)
            ).fetchone()
        return Area(**dict(row)) if row else None

    def list_areas(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute("SELECT id, nome, taxa FROM areas ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def is_available(self, area: str, data: str) -> bool:
        self._validate_date(data)
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1",
                (area, data),
            ).fetchone()
        return row is None

    def create_reservation(
        self, apartamento: str, area: str, data: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        self._validate_date(data)
        self.initialize()
        with self.connect(immediate=True) as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT codigo, area, data FROM reservas WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return {"status": "criada", **dict(existing)}
            codigo = f"RSV-{uuid4().hex.upper()}"
            try:
                connection.execute(
                    "INSERT INTO reservas(codigo, apartamento, area, data, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (codigo, apartamento, area, data, idempotency_key),
                )
            except sqlite3.IntegrityError as exc:
                if "reservas.area, reservas.data" in str(exc):
                    return {"status": "indisponivel", "area": area, "data": data}
                raise
        return {"status": "criada", "codigo": codigo, "area": area, "data": data}

    def cancel_reservation(self, apartamento: str, area: str, data: str) -> dict[str, Any]:
        self._validate_date(data)
        self.initialize()
        with self.connect(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE reservas SET ativa = 0 "
                "WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1",
                (apartamento, area, data),
            )
        return {"status": "cancelada" if cursor.rowcount else "nao_encontrada", "area": area, "data": data}

    def list_reservations(self, apartamento: str) -> list[dict[str, Any]]:
        self.require_apartment(apartamento)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT codigo, area, data FROM reservas "
                "WHERE apartamento = ? AND ativa = 1 ORDER BY data, area",
                (apartamento,),
            ).fetchall()
        return [dict(row) for row in rows]

    def authorize_visitor(
        self, apartamento: str, nome: str, data: str, idempotency_key: str
    ) -> dict[str, Any]:
        self._validate_date(data)
        self.initialize()
        with self.connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT nome, data FROM visitantes WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return {"status": "autorizado", **dict(existing)}
            connection.execute(
                "INSERT INTO visitantes(apartamento, nome, data, idempotency_key) VALUES (?, ?, ?, ?)",
                (apartamento, nome, data, idempotency_key),
            )
        return {"status": "autorizado", "nome": nome, "data": data}

    def list_visitors(self, apartamento: str) -> list[dict[str, Any]]:
        self.require_apartment(apartamento)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
                (apartamento,),
            ).fetchall()
        return [dict(row) for row in rows]

    def register_confirmation(
        self,
        *,
        confirmation_id: str,
        session_id: str,
        invocation_id: str,
        agent_name: str,
        function_call_id: str,
        acao: str,
        detalhes: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self.initialize()
        with self.connect(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO confirmacoes"
                "(id, session_id, invocation_id, agent_name, function_call_id, acao, detalhes_json, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    confirmation_id,
                    session_id,
                    invocation_id,
                    agent_name,
                    function_call_id,
                    acao,
                    json.dumps(detalhes, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def pending_confirmations(self, session_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, acao, detalhes_json FROM confirmacoes "
                "WHERE session_id = ? AND status = 'pending' ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return [
            {"id": row["id"], "acao": row["acao"], "detalhes": json.loads(row["detalhes_json"])}
            for row in rows
        ]

    def get_pending_confirmation(self, session_id: str, confirmation_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM confirmacoes WHERE id = ? AND session_id = ? AND status = 'pending'",
                (confirmation_id, session_id),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["detalhes"] = json.loads(result.pop("detalhes_json"))
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def finish_confirmation(self, confirmation_id: str, confirmed: bool) -> None:
        with self.connect(immediate=True) as connection:
            connection.execute(
                "UPDATE confirmacoes SET status = ? WHERE id = ? AND status = 'pending'",
                ("approved" if confirmed else "denied", confirmation_id),
            )
