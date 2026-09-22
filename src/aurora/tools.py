from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from .domain import DomainRepository
from .regulation import RegulationIndex


def _apartment(tool_context: ToolContext) -> str:
    """Obtém a identidade imutável da sessão; nunca aceita valor do modelo."""
    apartamento = tool_context.state.get("apartamento")
    if not isinstance(apartamento, str) or not apartamento:
        raise ValueError("Sessão sem apartamento autenticado")
    return apartamento


def build_reservation_tools(repository: DomainRepository) -> list[Any]:
    def listar_areas() -> list[dict[str, Any]]:
        """Lista áreas comuns reserváveis, seus identificadores e taxas."""
        return repository.list_areas()

    def consultar_minhas_reservas(tool_context: ToolContext) -> list[dict[str, Any]]:
        """Lista somente as reservas ativas do apartamento autenticado."""
        return repository.list_reservations(_apartment(tool_context))

    def verificar_disponibilidade(area: str, data: str) -> dict[str, Any]:
        """Informa apenas se uma área está livre na data, sem revelar quem a reservou."""
        if repository.get_area(area) is None:
            return {"status": "area_inexistente"}
        return {"area": area, "data": data, "disponivel": repository.is_available(area, data)}

    def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
        """Reserva uma área para o apartamento autenticado; áreas pagas exigem confirmação sistêmica."""
        if repository.get_area(area) is None:
            return {"status": "area_inexistente"}
        return repository.create_reservation(
            _apartment(tool_context),
            area,
            data,
            idempotency_key=tool_context.function_call_id,
        )

    def exige_confirmacao(area: str, data: str, tool_context: ToolContext) -> bool:
        del data, tool_context
        found = repository.get_area(area)
        return bool(found and found.taxa > 0)

    def cancelar_minha_reserva(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
        """Cancela sem confirmação uma reserva do próprio apartamento autenticado."""
        return repository.cancel_reservation(_apartment(tool_context), area, data)

    return [
        listar_areas,
        consultar_minhas_reservas,
        verificar_disponibilidade,
        FunctionTool(reservar_area, require_confirmation=exige_confirmacao),
        cancelar_minha_reserva,
    ]


def build_visitor_tools(repository: DomainRepository) -> list[Any]:
    def consultar_meus_visitantes(tool_context: ToolContext) -> list[dict[str, Any]]:
        """Lista somente visitantes autorizados pelo apartamento autenticado."""
        return repository.list_visitors(_apartment(tool_context))

    def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
        """Autoriza visitante para o apartamento autenticado; sempre exige confirmação sistêmica."""
        return repository.authorize_visitor(
            _apartment(tool_context),
            nome,
            data,
            idempotency_key=tool_context.function_call_id or "",
        )

    return [
        consultar_meus_visitantes,
        FunctionTool(autorizar_visitante, require_confirmation=True),
    ]


def build_regulation_tools(index: RegulationIndex) -> list[Any]:
    def consultar_regulamento(pergunta: str) -> dict[str, Any]:
        """Busca até três artigos relevantes do regulamento para responder à pergunta."""
        return {"trechos": index.search(pergunta)}

    return [consultar_regulamento]

