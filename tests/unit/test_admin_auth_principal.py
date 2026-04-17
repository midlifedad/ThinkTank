"""ADMIN-REVIEW LO-01: unit tests for principal sanitization.

The audit-label principal returned by ``require_admin`` is derived from
the ``admin_user`` cookie set at login. Since that cookie round-trips
into SQL TEXT audit columns, HTML attributes, and log lines, the
sanitizer must strip characters that would cause escaping surprises or
encoding ambiguity.
"""

from thinktank.admin.auth import DEFAULT_ADMIN_PRINCIPAL, _sanitize_principal


class TestSanitizePrincipal:
    def test_none_falls_back_to_default(self) -> None:
        assert _sanitize_principal(None) == DEFAULT_ADMIN_PRINCIPAL

    def test_empty_string_falls_back_to_default(self) -> None:
        assert _sanitize_principal("") == DEFAULT_ADMIN_PRINCIPAL

    def test_whitespace_only_falls_back_to_default(self) -> None:
        assert _sanitize_principal("   ") == DEFAULT_ADMIN_PRINCIPAL

    def test_alphanumeric_preserved(self) -> None:
        assert _sanitize_principal("luna42") == "luna42"

    def test_email_like_preserved(self) -> None:
        assert _sanitize_principal("amir.haque@themany.com") == "amir.haque@themany.com"

    def test_allowed_punctuation_preserved(self) -> None:
        assert _sanitize_principal("luna_bot+ci-1") == "luna_bot+ci-1"

    def test_newlines_stripped(self) -> None:
        # Newlines could break log line framing.
        assert _sanitize_principal("lu\nna") == "luna"

    def test_semicolons_stripped(self) -> None:
        # Defence in depth against naive string concat in logs/templates.
        assert _sanitize_principal("luna; DROP TABLE") == "luna DROP TABLE"

    def test_angle_brackets_stripped(self) -> None:
        # Not an HTML-escape substitute but avoids obvious sigils.
        assert _sanitize_principal("<script>alert(1)</script>") == "scriptalert1script"

    def test_clamped_to_max_length(self) -> None:
        raw = "a" * 200
        result = _sanitize_principal(raw)
        assert len(result) == 64
        assert result == "a" * 64

    def test_all_stripped_falls_back_to_default(self) -> None:
        # Value that consists entirely of forbidden characters collapses
        # to empty and falls back to default.
        assert _sanitize_principal("!!!") == DEFAULT_ADMIN_PRINCIPAL
