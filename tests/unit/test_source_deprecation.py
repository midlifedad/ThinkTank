"""DATA-REVIEW M2: Source.thinker_id deprecation warning.

Asserts the SQLAlchemy ``set`` event listener on ``Source.thinker_id``
emits a DeprecationWarning when callers still assign a non-None value.
The column is kept (nullable) for backward compatibility; a follow-up
migration (DATA M5/L4) drops it entirely.
"""

import uuid
import warnings

import pytest

from thinktank.models.source import Source


class TestSourceThinkerIdDeprecation:
    def test_assigning_non_none_emits_warning(self) -> None:
        src = Source()
        with pytest.warns(DeprecationWarning, match="Source.thinker_id is deprecated"):
            src.thinker_id = uuid.uuid4()

    def test_assigning_none_does_not_warn(self) -> None:
        src = Source()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            src.thinker_id = None  # must not raise

    def test_factory_default_does_not_warn(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            Source()

    def test_repeated_same_value_does_not_warn(self) -> None:
        src = Source()
        tid = uuid.uuid4()
        with pytest.warns(DeprecationWarning):
            src.thinker_id = tid
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            src.thinker_id = tid  # no-op repeat must not re-warn
