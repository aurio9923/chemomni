from __future__ import annotations

from chemomni.llm.prompts import SYSTEM_PROMPT


def test_a_series_is_never_padded_with_the_parent() -> None:
    # gpt-6-luna filled its list with the parent in 4 of 6 runs; the tool throws those out, so the user got fewer
    assert "Never send the parent itself" in SYSTEM_PROMPT


def test_numbers_are_only_for_tabs_that_have_them() -> None:
    assert "no <compounds> block" in SYSTEM_PROMPT
