from pathlib import Path

from typer.testing import CliRunner

from jobpipe.cli import app


def test_packet_includes_recommendation_and_reason(initialized_workspace, runner: CliRunner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "helpdesk_clean.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0
    assert runner.invoke(app, ["analyze", "1"]).exit_code == 0
    assert runner.invoke(app, ["packet", "1"]).exit_code == 0
    text = Path("output/packets/1-help-desk-analyst-i-packet.md").read_text(encoding="utf-8")
    assert "Recommendation: Apply" in text
    assert "Role family: helpdesk" in text
    assert "Seniority: entry_level" in text


def test_packet_warns_for_scam(initialized_workspace, runner: CliRunner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "chat_support_scam.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0
    assert runner.invoke(app, ["analyze", "1"]).exit_code == 0
    assert runner.invoke(app, ["packet", "1"]).exit_code == 0
    text = Path("output/packets/1-remote-chat-agent-no-experience-required-packet.md").read_text(encoding="utf-8")
    assert "Recommendation: Skip" in text
    assert "high scam/quality risk" in text.lower()
    assert "multiple scam or low-quality signals" in text.lower()
