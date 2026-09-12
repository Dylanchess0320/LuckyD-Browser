"""Lucky's suggest-only initiative — covered to stay 100% green."""

from browser.browser_core.lucky import (
    ONE_LINER,
    greeting,
    suggest,
    system_prompt,
)


def test_system_prompt_is_lucky_and_suggest_only() -> None:
    prompt = system_prompt()
    assert "Lucky" in prompt
    assert "never act" in prompt.lower()
    assert "GitHub-flavored Markdown" in prompt


def test_greeting_follows_time_of_day() -> None:
    assert greeting(8).startswith("Good morning")
    assert greeting(13).startswith("Good afternoon")
    assert greeting(19).startswith("Good evening")
    assert greeting(23).startswith("Up late")
    assert greeting(3).startswith("Up late")
    assert "Lucky" in greeting(9)


def test_greeting_tolerates_bad_input() -> None:
    assert "Lucky" in greeting("morning")  # type: ignore[arg-type]
    assert "Lucky" in greeting(-1)


def test_suggest_priority_order() -> None:
    # Agent result beats everything else.
    assert "agent" in suggest(
        open_tabs=20, downloads_done=2, agent_finished=True, update_pending=True
    ).lower()
    # Update beats downloads and tab overload.
    assert "update" in suggest(
        open_tabs=20, downloads_done=2, update_pending=True
    ).lower()
    # Downloads beat tab overload.
    assert "download" in suggest(open_tabs=20, downloads_done=1).lower()
    # Tab overload only fires at 12+.
    assert "tabs" in suggest(open_tabs=12).lower()
    assert suggest(open_tabs=11) == ""


def test_suggest_stays_quiet_by_default() -> None:
    assert suggest() == ""
    assert suggest(open_tabs=3) == ""
    assert ONE_LINER.startswith("Lucky")


def test_dashboard_is_lucky_first_no_redundant_tile() -> None:
    """The visible new-tab page brands Lucky and drops the Antigravity tile
    (Antigravity is one of the mesh agent chips inside the terminal, so the
    dashboard tile was a duplicate launcher)."""
    from browser.browser_core.dashboard import dashboard_html

    html = dashboard_html()
    assert "Lucky" in html
    assert "ready" in html
    assert "Antigravity" not in html
    assert "/terminal?shell=mesh-agy" not in html
