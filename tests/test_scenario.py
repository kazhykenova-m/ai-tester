import pytest

from ai_tester.scenario import load_scenario


def test_load_scenario(tmp_path):
    f = tmp_path / "s.md"
    f.write_text("  Open the site  \n", encoding="utf-8")
    assert load_scenario(f) == "Open the site"


def test_empty_scenario_raises(tmp_path):
    f = tmp_path / "s.md"
    f.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scenario(f)
