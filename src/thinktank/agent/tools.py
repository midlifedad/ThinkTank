"""Tool definitions and execution for the ThinkTank chat agent.

Defines query_database (read-only SQL) and propose_action (mutation proposals),
plus execute_confirmed_action for running approved mutations.
"""

import re
import uuid
from datetime import datetime, UTC

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig
from thinktank.models.job import Job
from thinktank.models.review import LLMReview
from thinktank.models.source import Source
from thinktank.models.thinker import Thinker

AGENT_TOOLS: list[dict] = [
    {
        "name": "query_database",
        "description": (
            "Execute a read-only SQL SELECT query against the ThinkTank PostgreSQL database. "
            "Returns results as JSON rows. Only SELECT queries are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SELECT SQL query to execute. Must start with SELECT.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Brief explanation of what this query answers.",
                },
            },
            "required": ["sql", "explanation"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "Propose a state-changing action for the operator to confirm. "
            "The action will NOT be executed until the operator clicks Confirm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": [
                        "add_thinker",
                        "approve_source",
                        "reject_source",
                        "trigger_discovery",
                        "toggle_kill_switch",
                        "update_config",
                        "retry_job",
                        "cancel_job",
                    ],
                    "description": "The type of action to propose.",
                },
                "target": {
                    "type": "string",
                    "description": "Description of what this action acts on.",
                },
                "details": {
                    "type": "object",
                    "description": "Action parameters (varies by action_type).",
                },
                "explanation": {
                    "type": "string",
                    "description": "Why this action is being proposed.",
                },
            },
            "required": ["action_type", "target", "details", "explanation"],
        },
    },
]


async def execute_tool(tool_name: str, tool_input: dict, session: AsyncSession) -> dict:
    """Execute a tool call from the agent.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        session: Database session for queries.

    Returns:
        Dict with tool results or error.
    """
    if tool_name == "query_database":
        return await _execute_query(tool_input, session)
    elif tool_name == "propose_action":
        return _create_proposal(tool_input)
    else:
        return {"error": f"Unknown tool: {tool_name}"}


async def _execute_query(tool_input: dict, session: AsyncSession) -> dict:
    """Execute a read-only SQL query."""
    sql = tool_input.get("sql", "").strip()

    # Validate SELECT-only
    if not re.match(r"(?i)^SELECT\b", sql):
        return {"error": "Only SELECT queries are allowed. Use propose_action for mutations."}

    # Add LIMIT if not present
    if not re.search(r"(?i)\bLIMIT\b", sql):
        sql = sql.rstrip(";") + " LIMIT 50"

    try:
        result = await session.execute(text(sql))
        rows = [dict(row._mapping) for row in result.fetchall()]
        # Convert non-serializable types to strings
        for row in rows:
            for key, value in row.items():
                if isinstance(value, (datetime, uuid.UUID)):
                    row[key] = str(value)
        return {"rows": rows, "row_count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def _create_proposal(tool_input: dict) -> dict:
    """Create a proposal without executing it."""
    return {
        "proposal": {
            "action_type": tool_input["action_type"],
            "target": tool_input["target"],
            "details": tool_input["details"],
            "explanation": tool_input["explanation"],
        },
        "status": "awaiting_confirmation",
    }


def _generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug.strip("-")


async def execute_confirmed_action(
    action_type: str, details: dict, session: AsyncSession
) -> dict:
    """Execute a confirmed action after operator approval.

    Args:
        action_type: The type of action to execute.
        details: Action parameters.
        session: Database session for mutations.

    Returns:
        Dict with success status or error.
    """
    try:
        if action_type == "add_thinker":
            return await _action_add_thinker(details, session)
        elif action_type == "approve_source":
            return await _action_approve_source(details, session)
        elif action_type == "reject_source":
            return await _action_reject_source(details, session)
        elif action_type == "trigger_discovery":
            return await _action_trigger_discovery(details, session)
        elif action_type == "toggle_kill_switch":
            return await _action_toggle_kill_switch(session)
        elif action_type == "update_config":
            return await _action_update_config(details, session)
        elif action_type == "retry_job":
            return await _action_retry_job(details, session)
        elif action_type == "cancel_job":
            return await _action_cancel_job(details, session)
        else:
            return {"error": f"Unknown action type: {action_type}"}
    except Exception as e:
        return {"error": str(e)}


async def _action_add_thinker(details: dict, session: AsyncSession) -> dict:
    """Add a new thinker and create an LLM approval job."""
    name = details.get("name", "").strip()
    if not name:
        return {"error": "Thinker name is required"}

    slug = _generate_slug(name)
    thinker = Thinker(
        name=name,
        slug=slug,
        tier=3,
        bio="Added via chat agent",
        approval_status="awaiting_llm",
    )
    session.add(thinker)
    await session.flush()

    # Create LLM approval job
    job = Job(
        job_type="llm_approval_check",
        payload={"thinker_id": str(thinker.id)},
        status="pending",
        priority=5,
    )
    session.add(job)
    await session.commit()

    return {
        "success": True,
        "message": f"Thinker '{name}' added with slug '{slug}' (awaiting LLM approval). Approval job created.",
        "thinker_id": str(thinker.id),
    }


async def _action_approve_source(details: dict, session: AsyncSession) -> dict:
    """Approve a pending source with admin override audit trail."""
    source_id = details.get("source_id")
    if not source_id:
        return {"error": "source_id is required"}

    await session.execute(
        update(Source)
        .where(Source.id == uuid.UUID(str(source_id)))
        .values(approval_status="approved")
    )

    review = LLMReview(
        review_type="source_approval",
        trigger="admin_override",
        context_snapshot={"source_id": str(source_id), "via": "chat_agent"},
        prompt_used="Admin override via chat agent",
        decision="approved",
        decision_reasoning="Approved by operator via chat agent",
    )
    session.add(review)
    await session.commit()

    return {"success": True, "message": f"Source {source_id} approved (admin override logged)."}


async def _action_reject_source(details: dict, session: AsyncSession) -> dict:
    """Reject a pending source with admin override audit trail."""
    source_id = details.get("source_id")
    if not source_id:
        return {"error": "source_id is required"}

    await session.execute(
        update(Source)
        .where(Source.id == uuid.UUID(str(source_id)))
        .values(approval_status="rejected")
    )

    review = LLMReview(
        review_type="source_approval",
        trigger="admin_override",
        context_snapshot={"source_id": str(source_id), "via": "chat_agent"},
        prompt_used="Admin override via chat agent",
        decision="rejected",
        decision_reasoning=details.get("reason", "Rejected by operator via chat agent"),
    )
    session.add(review)
    await session.commit()

    return {"success": True, "message": f"Source {source_id} rejected (admin override logged)."}


async def _action_trigger_discovery(details: dict, session: AsyncSession) -> dict:
    """Trigger podcast discovery for a specific thinker."""
    thinker_id = details.get("thinker_id")
    if not thinker_id:
        return {"error": "thinker_id is required"}

    job = Job(
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker_id)},
        status="pending",
        priority=5,
    )
    session.add(job)
    await session.commit()

    return {"success": True, "message": f"Discovery job created for thinker {thinker_id}."}


async def _action_toggle_kill_switch(session: AsyncSession) -> dict:
    """Toggle the global worker kill switch."""
    result = await session.execute(
        select(SystemConfig.value).where(SystemConfig.key == "workers_active")
    )
    current = result.scalar_one_or_none()

    # Default to True if not set, then flip
    current_bool = bool(current) if current is not None else True
    new_value = not current_bool

    existing = await session.execute(
        select(SystemConfig).where(SystemConfig.key == "workers_active")
    )
    row = existing.scalar_one_or_none()
    if row:
        row.value = new_value
        row.set_by = "chat_agent"
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    else:
        config = SystemConfig(
            key="workers_active",
            value=new_value,
            set_by="chat_agent",
        )
        session.add(config)

    await session.commit()

    state = "ACTIVE" if new_value else "STOPPED"
    return {"success": True, "message": f"Kill switch toggled. Workers are now {state}.", "workers_active": new_value}


async def _action_update_config(details: dict, session: AsyncSession) -> dict:
    """Update a system configuration value."""
    key = details.get("key")
    value = details.get("value")
    if not key:
        return {"error": "Config key is required"}

    existing = await session.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )
    row = existing.scalar_one_or_none()
    if row:
        row.value = value
        row.set_by = "chat_agent"
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    else:
        config = SystemConfig(
            key=key,
            value=value,
            set_by="chat_agent",
        )
        session.add(config)

    await session.commit()
    return {"success": True, "message": f"Config '{key}' updated."}


async def _action_retry_job(details: dict, session: AsyncSession) -> dict:
    """Retry a failed job by creating a new pending copy."""
    job_id = details.get("job_id")
    if not job_id:
        return {"error": "job_id is required"}

    result = await session.execute(
        select(Job).where(Job.id == uuid.UUID(str(job_id)))
    )
    original = result.scalar_one_or_none()
    if not original:
        return {"error": f"Job {job_id} not found"}

    new_job = Job(
        job_type=original.job_type,
        payload=original.payload,
        status="pending",
        priority=original.priority,
    )
    session.add(new_job)
    await session.commit()

    return {
        "success": True,
        "message": f"Retry job created for {original.job_type} (original: {job_id}).",
        "new_job_id": str(new_job.id),
    }


async def _action_cancel_job(details: dict, session: AsyncSession) -> dict:
    """Cancel a pending job."""
    job_id = details.get("job_id")
    if not job_id:
        return {"error": "job_id is required"}

    result = await session.execute(
        update(Job)
        .where(Job.id == uuid.UUID(str(job_id)), Job.status == "pending")
        .values(status="cancelled")
        .returning(Job.id)
    )
    cancelled = result.scalar_one_or_none()

    if cancelled:
        await session.commit()
        return {"success": True, "message": f"Job {job_id} cancelled."}
    else:
        return {"error": f"Job {job_id} not found or not in pending status."}
