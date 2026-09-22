from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Any
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from .agents import APP_NAME, USER_ID, build_app
from .config import Settings
from .domain import DomainRepository


class SessionNotFound(Exception):
    pass


class PendingConfirmation(Exception):
    pass


class ConfirmationNotFound(Exception):
    pass


class AuroraRuntime:
    def __init__(self, settings: Settings, repository: DomainRepository):
        self.settings = settings
        self.repository = repository
        if settings.google_api_key:
            os.environ["GOOGLE_API_KEY"] = settings.google_api_key
        settings.aurora_session_db.parent.mkdir(parents=True, exist_ok=True)
        self.session_service = DatabaseSessionService(db_url=settings.session_db_url)
        self.app = build_app(settings, repository)
        self.runner = Runner(app=self.app, session_service=self.session_service)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def close(self) -> None:
        await self.runner.close()

    async def create_session(self, apartamento: str) -> str:
        session_id = uuid4().hex
        await self.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            state={"apartamento": apartamento},
        )
        return session_id

    async def get_session(self, session_id: str):
        session = await self.session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        if session is None:
            raise SessionNotFound(session_id)
        return session

    async def send_message(self, session_id: str, texto: str) -> dict[str, Any]:
        async with self._locks[session_id]:
            await self.get_session(session_id)
            if self.repository.pending_confirmations(session_id):
                raise PendingConfirmation(session_id)
            content = types.Content(role="user", parts=[types.Part(text=texto)])
            events = [
                event
                async for event in self.runner.run_async(
                    user_id=USER_ID,
                    session_id=session_id,
                    new_message=content,
                )
            ]
            self._capture_confirmations(session_id, events)
            return self._response(events, session_id)

    async def answer_confirmation(
        self, session_id: str, confirmation_id: str, confirmed: bool
    ) -> dict[str, Any]:
        async with self._locks[session_id]:
            await self.get_session(session_id)
            pending = self.repository.get_pending_confirmation(session_id, confirmation_id)
            if pending is None:
                raise ConfirmationNotFound(confirmation_id)
            content = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmation_id,
                            name="adk_request_confirmation",
                            response={"confirmed": confirmed},
                        )
                    )
                ],
            )
            events = [
                event
                async for event in self.runner.run_async(
                    user_id=USER_ID,
                    session_id=session_id,
                    invocation_id=pending["invocation_id"],
                    new_message=content,
                )
            ]
            self.repository.finish_confirmation(confirmation_id, confirmed)
            self._capture_confirmations(session_id, events)
            return self._response(events, session_id)

    async def events(self, session_id: str) -> list[dict[str, Any]]:
        session = await self.get_session(session_id)
        return [
            event.model_dump(mode="json", by_alias=True, exclude_none=True)
            for event in session.events
        ]

    def _capture_confirmations(self, session_id: str, events: list[Any]) -> None:
        for event in events:
            for function_call in event.get_function_calls():
                if function_call.name != "adk_request_confirmation":
                    continue
                args = function_call.args or {}
                original = args.get("originalFunctionCall", {})
                original_args = original.get("args", {})
                action = original.get("name", "acao")
                details = self._confirmation_details(action, original_args)
                confirmation = args.get("toolConfirmation", {})
                self.repository.register_confirmation(
                    confirmation_id=function_call.id,
                    session_id=session_id,
                    invocation_id=event.invocation_id,
                    agent_name=event.author,
                    function_call_id=original.get("id", ""),
                    acao=action,
                    detalhes=details,
                    payload=confirmation.get("payload") or {},
                )

    @staticmethod
    def _confirmation_details(action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "reservar_area":
            return {"area": args.get("area"), "data": args.get("data")}
        if action == "autorizar_visitante":
            return {"nome": args.get("nome"), "data": args.get("data")}
        return dict(args)

    def _response(self, events: list[Any], session_id: str) -> dict[str, Any]:
        texts: list[str] = []
        for event in events:
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.text and not part.thought:
                    texts.append(part.text)
        return {
            "resposta": texts[-1] if texts else "",
            "confirmacoes_pendentes": self.repository.pending_confirmations(session_id),
        }

