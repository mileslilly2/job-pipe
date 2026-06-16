from pathlib import Path

from typer.testing import CliRunner

from jobpipe.cli import app


def test_packet_recommends_apply_for_clean_helpdesk(initialized_workspace, runner: CliRunner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "helpdesk_clean.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0
    assert runner.invoke(app, ["analyze", "1"]).exit_code == 0
    result = runner.invoke(app, ["packet", "1"])
    assert result.exit_code == 0, result.stdout
    packet_path = Path("output/packets/1-help-desk-analyst-i-packet.md")
    text = packet_path.read_text(encoding="utf-8")
    assert "Recommendation: Apply" in text


def test_packet_warns_for_scam(initialized_workspace, runner: CliRunner) -> None:
    fixture = Path(__file__).parent / "fixtures" / "chat_support_scam.txt"
    assert runner.invoke(app, ["add-text", "--file", str(fixture)]).exit_code == 0
    assert runner.invoke(app, ["analyze", "1"]).exit_code == 0
    assert runner.invoke(app, ["packet", "1"]).exit_code == 0
    packet_path = Path("output/packets/1-remote-chat-support-specialist-packet.md")
    text = packet_path.read_text(encoding="utf-8")
    assert "Recommendation: Skip" in text or "Recommendation: Maybe apply" in text
    assert "Telegram interview" in text or "WhatsApp interview" in text
