from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import get_settings
from .domain import ApartmentNotFound, DomainRepository
from .runtime import (
    AuroraRuntime,
    ConfirmationNotFound,
    PendingConfirmation,
    SessionNotFound,
)


class SessionCreate(BaseModel):
    apartamento: str = Field(min_length=1)


class SessionCreated(BaseModel):
    session_id: str


class MessageCreate(BaseModel):
    texto: str = Field(min_length=1)


class ConfirmationAnswer(BaseModel):
    id: str = Field(min_length=1)
    confirmado: bool


class PendingConfirmationModel(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class ConversationResponse(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[PendingConfirmationModel]


def _repository() -> DomainRepository:
    settings = get_settings()
    return DomainRepository(settings.aurora_domain_db, settings.data_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = _repository()
    repository.initialize()
    runtime = AuroraRuntime(get_settings(), repository)
    app.state.repository = repository
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


def repository(request: Request) -> DomainRepository:
    return request.app.state.repository


def runtime(request: Request) -> AuroraRuntime:
    return request.app.state.runtime


RepositoryDep = Annotated[DomainRepository, Depends(repository)]
RuntimeDep = Annotated[AuroraRuntime, Depends(runtime)]


@app.post("/sessoes", response_model=SessionCreated, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, repo: RepositoryDep, rt: RuntimeDep):
    if not repo.apartment_exists(body.apartamento):
        raise HTTPException(status_code=422, detail="Apartamento inexistente")
    return {"session_id": await rt.create_session(body.apartamento)}


@app.post("/sessoes/{session_id}/mensagens", response_model=ConversationResponse)
async def send_message(session_id: str, body: MessageCreate, rt: RuntimeDep):
    try:
        return await rt.send_message(session_id, body.texto)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="Sessão inexistente") from None
    except PendingConfirmation:
        raise HTTPException(status_code=409, detail="A sessão possui confirmação pendente") from None


@app.post("/sessoes/{session_id}/confirmacoes", response_model=ConversationResponse)
async def answer_confirmation(session_id: str, body: ConfirmationAnswer, rt: RuntimeDep):
    try:
        return await rt.answer_confirmation(session_id, body.id, body.confirmado)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="Sessão inexistente") from None
    except ConfirmationNotFound:
        raise HTTPException(status_code=409, detail="Confirmação não está pendente nesta sessão") from None


@app.get("/sessoes/{session_id}/eventos")
async def get_events(session_id: str, rt: RuntimeDep):
    try:
        return await rt.events(session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="Sessão inexistente") from None


@app.get("/apartamentos/{apartamento}/reservas")
def get_reservations(apartamento: str, repo: RepositoryDep):
    try:
        return repo.list_reservations(apartamento)
    except ApartmentNotFound:
        raise HTTPException(status_code=404, detail="Apartamento inexistente") from None


@app.get("/apartamentos/{apartamento}/visitantes")
def get_visitors(apartamento: str, repo: RepositoryDep):
    try:
        return repo.list_visitors(apartamento)
    except ApartmentNotFound:
        raise HTTPException(status_code=404, detail="Apartamento inexistente") from None


def run() -> None:
    uvicorn.run("aurora.api:app", host="0.0.0.0", port=8000)

