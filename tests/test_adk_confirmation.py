from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from aurora.domain import DomainRepository
from aurora.tools import build_reservation_tools


class ReservationModel(BaseLlm):
    calls: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        self.calls += 1
        if self.calls == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="reserve-call",
                                name="reservar_area",
                                args={"area": "salao-de-festas", "data": "2030-04-20"},
                            )
                        )
                    ],
                )
            )
        else:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="Reserva concluída.")])
            )


async def test_native_confirmation_resumes_with_persistent_session(
    repository: DomainRepository, tmp_path: Path
) -> None:
    model = ReservationModel(model="scripted")
    agent = Agent(
        name="especialista_reservas",
        model=model,
        instruction="Use a tool.",
        tools=build_reservation_tools(repository),
    )
    adk_app = App(
        name="confirmation_test",
        root_agent=agent,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    service = DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    )
    await service.create_session(
        app_name="confirmation_test",
        user_id="user",
        session_id="session",
        state={"apartamento": "101"},
    )
    runner = Runner(app=adk_app, session_service=service)
    first_events = [
        event
        async for event in runner.run_async(
            user_id="user",
            session_id="session",
            new_message=types.Content(role="user", parts=[types.Part(text="reserve")]),
        )
    ]
    request_event = next(
        event
        for event in first_events
        if any(call.name == "adk_request_confirmation" for call in event.get_function_calls())
    )
    confirmation_call = request_event.get_function_calls()[0]
    assert repository.is_available("salao-de-festas", "2030-04-20")

    second_events = [
        event
        async for event in runner.run_async(
            user_id="user",
            session_id="session",
            invocation_id=request_event.invocation_id,
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmation_call.id,
                            name="adk_request_confirmation",
                            response={"confirmed": True},
                        )
                    )
                ],
            ),
        )
    ]
    assert any(
        part.text == "Reserva concluída."
        for event in second_events
        if event.content
        for part in event.content.parts
    )
    assert not repository.is_available("salao-de-festas", "2030-04-20")
    await runner.close()

    restarted_service = DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    )
    persisted = await restarted_service.get_session(
        app_name="confirmation_test", user_id="user", session_id="session"
    )
    assert persisted is not None
    assert len(persisted.events) >= len(first_events) + len(second_events)
