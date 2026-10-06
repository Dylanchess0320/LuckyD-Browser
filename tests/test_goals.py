import pytest

from core.goals import (
    Goal,
    GoalStore,
    _format_budget,
    goal_from_session,
    goal_into_session,
    parse_budget,
    parse_goal_command,
)


def test_goal_serialization():
    # Test empty dict / None
    assert Goal.from_dict(None) is None
    assert Goal.from_dict({}) is None

    # Test basic instantiation and to_dict
    g = Goal(text="Fix tests", budget=1000, paused=False, spent=100)
    data = g.to_dict()
    assert data == {"text": "Fix tests", "budget": 1000, "paused": False, "spent": 100}

    # Test from_dict
    g2 = Goal.from_dict(data)
    assert g2 is not None
    assert g2.text == "Fix tests"
    assert g2.budget == 1000
    assert g2.paused is False
    assert g2.spent == 100

    # Test from_dict missing fields
    g3 = Goal.from_dict({"text": "Test"})
    assert g3 is not None
    assert g3.text == "Test"
    assert g3.budget is None
    assert g3.spent == 0

    # Test from_dict invalid type fallback
    assert Goal.from_dict({"text": None}) is None


def test_goal_status_line():
    g = Goal(text="Fix tests")
    assert g.status_line() == "Goal (active): Fix tests"

    g.paused = True
    assert g.status_line() == "Goal (paused): Fix tests"

    g.budget = 5000
    g.spent = 1000
    assert g.status_line() == "Goal (paused, budget 5K, spent 1000): Fix tests"


def test_goal_remaining():
    g = Goal(text="Fix tests")
    assert g.remaining() is None

    g.budget = 1000
    assert g.remaining() == 1000

    g.spent = 500
    assert g.remaining() == 500

    g.spent = 1500
    assert g.remaining() == 0


def test_format_budget():
    assert _format_budget(500) == "500"
    assert _format_budget(1000) == "1K"
    assert _format_budget(1500) == "1500"
    assert _format_budget(1000000) == "1M"
    assert _format_budget(1500000) == "1500K"


def test_parse_budget():
    assert parse_budget("clear") is None
    assert parse_budget("  CLEAR  ") is None

    assert parse_budget("500") == 500
    assert parse_budget("1K") == 1000
    assert parse_budget("2.5K") == 2500
    assert parse_budget("1M") == 1000000
    assert parse_budget("1.5M") == 1500000

    with pytest.raises(ValueError, match="empty budget"):
        parse_budget("")
    with pytest.raises(ValueError, match="empty budget"):
        parse_budget("  ")

    with pytest.raises(ValueError, match="invalid budget: 'foo'"):
        parse_budget("foo")
    with pytest.raises(ValueError, match="must be >= 0"):
        parse_budget("-100")


def test_parse_goal_command():
    assert parse_goal_command("") == ("status", "")
    assert parse_goal_command("   ") == ("status", "")
    assert parse_goal_command("status") == ("status", "")

    assert parse_goal_command("pause") == ("pause", "")
    assert parse_goal_command("resume") == ("resume", "")
    assert parse_goal_command("clear") == ("clear", "")

    assert parse_goal_command("budget") == ("budget", "")
    assert parse_goal_command("budget 50K") == ("budget", "50K")
    assert parse_goal_command("budget=50K") == ("budget", "50K")

    assert parse_goal_command("edit") == ("edit", "")
    assert parse_goal_command("edit Make things work") == ("edit", "Make things work")

    assert parse_goal_command("Fix all bugs") == ("set", "Fix all bugs")


def test_goalstore_methods():
    store = GoalStore()

    assert store.edit("test") is None
    assert store.set_budget(100) is None
    assert store.pause() is None
    assert store.resume() is None
    assert store.to_dict() is None

    goal = store.set("Fix tests")
    assert goal.text == "Fix tests"
    assert store.goal == goal

    assert store.describe() == "Goal (active): Fix tests"

    store.edit("  Fix tests and lint  ")
    assert store.goal.text == "Fix tests and lint"

    store.set_budget(1000)
    assert store.goal.budget == 1000

    store.pause()
    assert store.goal.paused is True

    store.resume()
    assert store.goal.paused is False

    store.add_spent(150)
    assert store.goal.spent == 150
    # Invalid types handled safely
    store.add_spent(None)
    assert store.goal.spent == 150

    d = store.to_dict()
    assert d["text"] == "Fix tests and lint"

    store.clear()
    assert store.goal is None

    store.restore(d)
    assert store.goal is not None
    assert store.goal.text == "Fix tests and lint"

    store.clear()
    assert store.describe() == "No active goal. Set one with /goal <text>."


def test_goalstore_handle_command():
    store = GoalStore()

    # status without goal
    assert store.handle_command("") == "No active goal. Set one with /goal <text>."
    assert store.handle_command("status") == "No active goal. Set one with /goal <text>."

    # errors when no goal exists
    assert store.handle_command("budget") == "No active goal. Set one with /goal <text> first."
    assert store.handle_command("pause") == "No active goal to pause."
    assert store.handle_command("resume") == "No active goal to resume."
    assert store.handle_command("edit") == "No active goal to edit."
    assert store.handle_command("clear") == "No active goal."

    # set
    assert store.handle_command("Make tests pass") == "Goal set: Make tests pass"
    assert store.goal.text == "Make tests pass"

    # edit
    assert store.handle_command("edit") == "Current goal: Make tests pass"
    assert store.handle_command("edit Make all tests pass") == "Goal updated: Make all tests pass"
    assert store.goal.text == "Make all tests pass"

    # pause / resume
    assert store.handle_command("pause") == "Goal paused."
    assert store.goal.paused is True
    assert store.handle_command("resume") == "Goal resumed."
    assert store.goal.paused is False

    # budget
    assert store.handle_command("budget") == "No budget set on the current goal."
    assert store.handle_command("budget 50K") == "Goal budget set to 50K."
    assert store.goal.budget == 50000
    assert store.handle_command("budget") == "Budget: 50K (spent 0)."
    assert (
        store.handle_command("budget invalid")
        == "invalid budget: 'invalid' (try 50K, 2M, or clear)"
    )
    assert store.handle_command("budget clear") == "Goal budget cleared."
    assert store.goal.budget is None

    # status with goal
    assert store.handle_command("status") == "Goal (active): Make all tests pass"

    # clear
    assert store.handle_command("clear") == "Goal cleared."
    assert store.goal is None


def test_session_helpers():
    # from session empty
    assert goal_from_session(None) is None
    assert goal_from_session({}) is None

    # from session top-level
    g1 = goal_from_session({"goal": {"text": "Test goal 1"}})
    assert g1 is not None
    assert g1.text == "Test goal 1"

    # from session meta
    g2 = goal_from_session({"meta": {"goal": {"text": "Test goal 2"}}})
    assert g2 is not None
    assert g2.text == "Test goal 2"

    # into session
    g = Goal(text="My session goal")
    payload = {}
    result = goal_into_session(payload, g)
    assert result["goal"]["text"] == "My session goal"
    assert result["meta"]["goal"]["text"] == "My session goal"

    # into session with clear
    result = goal_into_session(payload, None)
    assert result["goal"] is None
    assert "goal" not in result["meta"]
