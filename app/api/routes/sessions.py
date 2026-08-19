"""HTTP layer only: parse, delegate, serialise. No business logic here."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ChatServiceDep, PricingDep, require_api_key
from app.schemas.chat import (
    MessageCreate,
    MessageOut,
    ModelOut,
    SendMessageOut,
    SessionCreate,
    SessionDetailOut,
    SessionOut,
    UsageOut,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, chat: ChatServiceDep) -> SessionOut:
    session = await chat.create_session(
        model=payload.model, title=payload.title, system_prompt=payload.system_prompt
    )
    return SessionOut.model_validate(session)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    chat: ChatServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SessionOut]:
    sessions = await chat.repo.list_sessions(limit=limit, offset=offset)
    return [SessionOut.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def get_session(session_id: str, chat: ChatServiceDep) -> SessionDetailOut:
    """Full history plus the accumulated cost of the session."""
    session = await chat.get_session_or_404(session_id)
    messages = await chat.repo.list_messages(session_id)
    return SessionDetailOut(
        **SessionOut.model_validate(session).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=SendMessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    session_id: str, payload: MessageCreate, chat: ChatServiceDep
) -> SendMessageOut:
    result = await chat.send_message(session_id, payload.content)
    return SendMessageOut(
        session_id=session_id,
        user_message=MessageOut.model_validate(result.user_message),
        assistant_message=MessageOut.model_validate(result.assistant_message),
        usage=UsageOut(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cached_tokens=result.cached_tokens,
            total_tokens=result.total_tokens,
        ),
        cost=result.cost,
        total_accumulated_cost=result.session_total_cost,
        context_messages=result.context_messages,
        unpriced_usage=result.unpriced_usage,
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, chat: ChatServiceDep) -> list[MessageOut]:
    await chat.get_session_or_404(session_id)
    messages = await chat.repo.list_messages(session_id)
    return [MessageOut.model_validate(m) for m in messages]


@router.get("/models", response_model=list[ModelOut])
async def list_models(pricing: PricingDep) -> list[ModelOut]:
    """Models this service can price, with the tariffs in use."""
    out = []
    for name in pricing.known_models():
        price = pricing.price_for(name)
        out.append(
            ModelOut(
                model=name,
                input_per_1m=price.input,
                cached_input_per_1m=price.cached_input,
                output_per_1m=price.output,
                currency=pricing.currency,
            )
        )
    return out
