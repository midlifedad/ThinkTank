"""Integration tests for get_secret JSONB unwrap behavior.

INTEGRATIONS-REVIEW C-01: `str(row) if not isinstance(row, str) else row`
on a JSONB dict returns the literal Python repr (e.g. "{'value': 'sk-...'}"),
which is then passed as the API key -> all downstream API calls fail with
authentication errors. Also: `if row is not None and row:` treats empty
strings as "has value", skipping env fallback.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig
from thinktank.secrets import get_secret

pytestmark = pytest.mark.anyio


async def test_get_secret_unwraps_jsonb_dict(session: AsyncSession) -> None:
    """JSONB dict {"value": "sk-..."} must unwrap to the inner string,
    not be stringified to "{'value': 'sk-...'}".
    """
    session.add(
        SystemConfig(
            key="secret_anthropic_api_key",
            value={"value": "sk-ant-dict-wrapped"},
            set_by="test",
        )
    )
    await session.commit()

    result = await get_secret(session, "anthropic_api_key")

    assert result == "sk-ant-dict-wrapped"


async def test_get_secret_returns_string_value_directly(session: AsyncSession) -> None:
    """JSONB plain-string value must be returned as-is."""
    session.add(
        SystemConfig(
            key="secret_plain_key",
            value="plain-string-value",
            set_by="test",
        )
    )
    await session.commit()

    result = await get_secret(session, "plain_key")

    assert result == "plain-string-value"


async def test_get_secret_falls_back_to_env_when_row_missing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No DB row -> env var fallback using uppercase name."""
    monkeypatch.setenv("MISSING_KEY", "from-env")

    result = await get_secret(session, "missing_key")

    assert result == "from-env"


async def test_get_secret_empty_string_falls_back_to_env(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty-string DB value must NOT shadow a real env var."""
    session.add(
        SystemConfig(
            key="secret_empty_key",
            value="",
            set_by="test",
        )
    )
    await session.commit()
    monkeypatch.setenv("EMPTY_KEY", "from-env")

    result = await get_secret(session, "empty_key")

    assert result == "from-env"


async def test_get_secret_empty_dict_value_falls_back_to_env(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSONB dict with missing/empty "value" must fall through to env."""
    session.add(
        SystemConfig(
            key="secret_empty_dict",
            value={"value": ""},
            set_by="test",
        )
    )
    await session.commit()
    monkeypatch.setenv("EMPTY_DICT", "from-env")

    result = await get_secret(session, "empty_dict")

    assert result == "from-env"


async def test_get_secret_returns_none_when_nothing_set(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No DB row and no env var -> None."""
    monkeypatch.delenv("NEVER_SET", raising=False)

    result = await get_secret(session, "never_set")

    assert result is None
