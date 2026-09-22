from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from aurora.domain import DomainRepository
from aurora.tools import build_reservation_tools, build_visitor_tools


def test_initial_data_and_restore(repository: DomainRepository) -> None:
    assert repository.list_reservations("101") == [
        {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
    ]
    assert repository.list_visitors("302") == [
        {"nome": "Marina Duarte", "data": "2030-03-16"}
    ]
    repository.cancel_reservation("101", "quadra", "2030-03-09")
    repository.restore()
    assert repository.list_reservations("101")[0]["codigo"] == "RSV-1377"


def test_cancellation_is_scoped_and_codes_are_not_reused(repository: DomainRepository) -> None:
    assert repository.cancel_reservation("101", "salao-de-festas", "2030-03-16")["status"] == "nao_encontrada"
    assert repository.list_reservations("302")[0]["codigo"] == "RSV-4821"

    created = repository.create_reservation("101", "quadra", "2030-04-06", "first")
    repository.cancel_reservation("101", "quadra", "2030-04-06")
    second = repository.create_reservation("101", "quadra", "2030-04-06", "second")
    assert created["codigo"] != second["codigo"]
    assert created["codigo"] not in {"RSV-1377", "RSV-4821", "RSV-2950"}


def test_idempotent_operation(repository: DomainRepository) -> None:
    first = repository.create_reservation("101", "quadra", "2030-04-06", "same-call")
    second = repository.create_reservation("101", "quadra", "2030-04-06", "same-call")
    assert first == second
    assert len([r for r in repository.list_reservations("101") if r["data"] == "2030-04-06"]) == 1


def test_concurrent_reservation_has_exactly_one_winner(repository: DomainRepository) -> None:
    def reserve(apartment: str) -> dict[str, str]:
        return repository.create_reservation(
            apartment, "salao-de-festas", "2030-05-11", f"call-{apartment}"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ["101", "201"]))

    assert sorted(result["status"] for result in results) == ["criada", "indisponivel"]
    total = sum(
        reservation["area"] == "salao-de-festas" and reservation["data"] == "2030-05-11"
        for apartment in ("101", "201")
        for reservation in repository.list_reservations(apartment)
    )
    assert total == 1


def test_confirmation_registry_is_session_scoped(repository: DomainRepository) -> None:
    repository.register_confirmation(
        confirmation_id="confirmation-1",
        session_id="session-1",
        invocation_id="invocation-1",
        agent_name="especialista_reservas",
        function_call_id="call-1",
        acao="reservar_area",
        detalhes={"area": "salao-de-festas", "data": "2030-04-20"},
        payload={},
    )
    assert repository.get_pending_confirmation("session-2", "confirmation-1") is None
    assert repository.get_pending_confirmation("session-1", "confirmation-1") is not None
    repository.finish_confirmation("confirmation-1", True)
    assert repository.get_pending_confirmation("session-1", "confirmation-1") is None


def test_conversation_tools_never_accept_apartment(repository: DomainRepository) -> None:
    tools = [*build_reservation_tools(repository), *build_visitor_tools(repository)]
    for tool in tools:
        declaration = tool._get_declaration() if hasattr(tool, "_get_declaration") else None
        if declaration and declaration.parameters_json_schema:
            properties = declaration.parameters_json_schema.get("properties", {})
            assert "apartamento" not in properties

