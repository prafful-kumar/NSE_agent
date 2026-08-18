from __future__ import annotations

"""research_memory_context node: serve Phase 4A news/research-memory facts
from PostgreSQL only.

Architecture:
    LiveMint/ET RSS ──[sync-news CLI]──► PostgreSQL (news_items, news_events,
                                          research_notes, thesis_changes,
                                          catalysts, risk_observations)
                                                    │
                                    research_memory_context_node reads here
                                                    │
                       LangGraph state["company_facts"][symbol]["research_memory"]

This node NEVER polls RSS feeds during normal reasoning — ingestion is a
separate, explicitly triggered pipeline (sync-news CLI / a future
scheduler), exactly like factual_company_context_node never calls NSE
directly. Default lookback is 90 days — the widest of the "what changed in
the last 7/30/90 days" queries a user is expected to ask — since every item
still carries its own timestamp, a caller can filter further within that
window without another DB round-trip.

NewsEvents are discovered signal, not verified fact: evidence built here is
always is_confirmed=False for events, unlike the Tier-1 facts served by
factual_company_context_node. ResearchNote/ThesisChange evidence is marked
is_confirmed=True because a human explicitly recorded it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.news import NewsCompanyLinkRepository, NewsEventRepository
from investing_agent.db.repositories.research_memory import (
    CatalystRepository,
    ResearchNoteRepository,
    RiskObservationRepository,
    ThesisChangeRepository,
)

log = get_logger(__name__)

LOOKBACK = timedelta(days=90)


async def research_memory_context_node(
    state: InvestmentState,
    session: AsyncSession,
) -> dict[str, Any]:
    symbols = state.get("symbols", [])
    company_facts = dict(state.get("company_facts", {}))
    evidence = list(state.get("evidence", []))
    news_items_out = list(state.get("news", []))
    freshness = dict(state.get("data_freshness", {}))

    company_repo = CompanyRepository(session)
    event_repo = NewsEventRepository(session)
    link_repo = NewsCompanyLinkRepository(session)
    note_repo = ResearchNoteRepository(session)
    thesis_change_repo = ThesisChangeRepository(session)
    catalyst_repo = CatalystRepository(session)
    risk_repo = RiskObservationRepository(session)

    since = datetime.now(UTC) - LOOKBACK
    latest_activity_at: datetime | None = None

    for symbol in symbols:
        company = await company_repo.get_by_symbol(symbol)
        if not company:
            log.info("research_memory_context.no_company", symbol=symbol)
            continue

        events = await event_repo.list_by_company(company.id, since=since)
        links = await link_repo.list_by_company_since(company.id, since)
        notes = await note_repo.list_by_company(company.id, since=since)
        thesis_changes = await thesis_change_repo.list_by_company(company.id, since=since)
        catalysts = await catalyst_repo.list_active(company.id)
        risks = await risk_repo.list_active(company.id)

        company_facts.setdefault(symbol, {})
        company_facts[symbol]["research_memory"] = {
            "recent_events": [_event_to_dict(e) for e in events],
            "research_notes": [_note_to_dict(n) for n in notes],
            "thesis_changes": [_thesis_change_to_dict(c) for c in thesis_changes],
            "active_catalysts": [_catalyst_to_dict(c) for c in catalysts],
            "active_risks": [_risk_to_dict(r) for r in risks],
        }

        for event in events:
            news_items_out.append(_event_to_state_news(symbol, event))
            evidence.append(_event_evidence(symbol, event))
            if latest_activity_at is None or event.last_seen_at > latest_activity_at:
                latest_activity_at = event.last_seen_at
        for note in notes:
            evidence.append(_note_evidence(symbol, note))
        for change in thesis_changes:
            evidence.append(_thesis_change_evidence(symbol, change))

        log.info(
            "research_memory_context.served_from_db",
            symbol=symbol,
            events_count=len(events),
            company_links_count=len(links),
            notes_count=len(notes),
            thesis_changes_count=len(thesis_changes),
            active_catalysts_count=len(catalysts),
            active_risks_count=len(risks),
        )

    if latest_activity_at is not None:
        freshness["news"] = latest_activity_at.isoformat()

    return {
        "company_facts": company_facts,
        "news": news_items_out,
        "evidence": evidence,
        "data_freshness": freshness,
    }


def _event_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "event_date": row.event_date.isoformat() if row.event_date else None,
        "first_seen_at": row.first_seen_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
        "importance": row.importance,
        "status": row.status,
        "representative_headline": row.representative_headline,
    }


def _event_to_state_news(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "headline": row.representative_headline,
        "event_type": row.event_type,
        "last_seen_at": row.last_seen_at.isoformat(),
        "news_event_id": str(row.id),
    }


def _event_evidence(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "source": "news_event",
        "published_at": row.last_seen_at.isoformat(),
        "url": None,
        "tier": 2,
        "category": "news",
        "excerpt": f"{symbol}: {row.representative_headline}",
        "is_confirmed": False,
    }


def _note_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "note_type": row.note_type,
        "text": row.text,
        "effective_at": row.effective_at.isoformat(),
        "created_by": row.created_by,
    }


def _note_evidence(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "source": "research_note",
        "published_at": row.effective_at.isoformat(),
        "url": None,
        "tier": 2,
        "category": "research",
        "excerpt": f"{symbol}: {row.text[:200]}",
        "is_confirmed": True,
    }


def _thesis_change_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "change_type": row.change_type,
        "reason": row.reason,
        "effective_at": row.effective_at.isoformat(),
    }


def _thesis_change_evidence(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "source": "thesis_change",
        "published_at": row.effective_at.isoformat(),
        "url": None,
        "tier": 2,
        "category": "research",
        "excerpt": f"{symbol} thesis {row.change_type}: {row.reason[:200]}",
        "is_confirmed": True,
    }


def _catalyst_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "description": row.description,
        "catalyst_type": row.catalyst_type,
        "status": row.status,
        "expected_by": row.expected_by,
    }


def _risk_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "description": row.description,
        "risk_type": row.risk_type,
        "severity": row.severity,
        "status": row.status,
    }
