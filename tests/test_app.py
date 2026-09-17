from pathlib import Path
import io

from streamlit.testing.v1 import AppTest
import streamlit as st
import pytest

from coaching.ai import CoachAI, AIError
from coaching.db import build_engine
from coaching.repository import Repository
from conftest import PASSWORD

APP = Path(__file__).resolve().parents[1] / "app.py"


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


@pytest.fixture
def app(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    at = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not at.exception
    repo = Repository(build_engine(url))
    yield at, repo
    repo.engine.dispose()


def log_in(at, username="coach_admin", password=PASSWORD):
    next(x for x in at.text_input if x.label == "Username").set_value(username)
    next(x for x in at.text_input if x.label == "Password").set_value(password)
    click(at, "Log in")


def test_admin_creates_and_opens_learner_then_typed_practice(app, monkeypatch, evaluation):
    at, repo = app
    monkeypatch.setattr(CoachAI, "evaluate", lambda self, attempt: evaluation)
    log_in(at)
    next(x for x in at.text_input if x.label == "Name").set_value("Alice")
    next(x for x in at.text_input if x.label == "New username").set_value("alice")
    next(x for x in at.text_input if x.label == "Initial password").set_value(PASSWORD)
    click(at, "Create account")
    principal = repo.authenticate("coach_admin", PASSWORD)
    alice = next(u for u in repo.list_users(principal) if u["username"] == "alice")
    at.button(key=f"open_{alice['id']}").click().run()
    assert not at.exception
    assert any("Coach view" in item.value for item in at.info)
    answer = next(x for x in at.text_area if x.label == "Your answer / editable transcript")
    answer.set_value("I prioritized the customer problem and measured the outcome.").run()
    click(at, "Submit answer")
    assert any("Saved to Progress" in item.value for item in at.success)
    assert len(repo.list_attempts(principal, alice["id"])) == 1
    at.radio(key="page").set_value("Progress").run()
    assert not at.exception
    assert any(m.value == "1" for m in at.metric)
    at.radio(key="page").set_value("Configuration").run()
    next(t for t in at.text_area if t.label == "Training goal").set_value("Prepare for product interviews")
    click(at, "Save configuration")
    assert repo.get_config(principal, alice["id"])["training_goal"] == "Prepare for product interviews"
    click(at, "Log out")
    log_in(at, "alice")
    assert not any(b.label == "Coach dashboard" for b in at.button)


def test_draft_survives_navigation_and_failed_answer_is_retryable(app, monkeypatch, evaluation):
    at, repo = app
    admin = repo.authenticate("coach_admin", PASSWORD)
    uid = repo.create_user(admin, "learner", PASSWORD, "Learner")
    log_in(at, "learner")
    next(t for t in at.text_area if t.label == "Your answer / editable transcript").set_value("A carefully written answer.").run()
    at.radio(key="page").set_value("Progress").run()
    at.radio(key="page").set_value("Practice").run()
    assert next(t for t in at.text_area if t.label == "Your answer / editable transcript").value == "A carefully written answer."

    def fail(*args):
        raise AIError("Temporary failure")

    monkeypatch.setattr(CoachAI, "evaluate", fail)
    click(at, "Submit answer")
    saved = repo.list_attempts(admin, uid)[0]
    assert saved["status"] == "failed" and saved["answer"] == "A carefully written answer."
    monkeypatch.setattr(CoachAI, "evaluate", lambda *args: evaluation)
    click(at, "Retry evaluation")
    assert repo.list_attempts(admin, uid)[0]["status"] == "completed"
    repo.set_active(admin, uid, False)
    at.run()
    assert "principal" not in at.session_state
    assert any("session has ended" in item.value for item in at.error)


def test_missing_key_keeps_app_usable_and_shows_saved_failure(app, monkeypatch):
    at, repo = app
    monkeypatch.setenv("OPENAI_API_KEY", "")
    admin = repo.authenticate("coach_admin", PASSWORD)
    uid = repo.create_user(admin, "learner", PASSWORD, "Learner")
    log_in(at, "learner")
    next(t for t in at.text_area if t.label == "Your answer / editable transcript").set_value("Saved without an API key.").run()
    click(at, "Submit answer")
    saved = repo.list_attempts(admin, uid)[0]
    assert saved["status"] == "failed"
    assert "OPENAI_API_KEY" in saved["error_message"]


def test_transcript_review_is_separate_and_final_edit_is_saved(app, monkeypatch, evaluation):
    at, repo = app
    admin = repo.authenticate("coach_admin", PASSWORD)
    uid = repo.create_user(admin, "speaker", PASSWORD, "Speaker")
    audio = io.BytesIO(b"RIFF-test-recording")
    audio.size = len(audio.getvalue())
    # AppTest does not expose browser microphone capture; substitute only the native
    # widget's return value and exercise the real review/submit UI around it.
    monkeypatch.setattr(st, "audio_input", lambda *args, **kwargs: audio)
    calls = {"transcribe": 0, "evaluate": 0}

    def transcribe(self, data):
        calls["transcribe"] += 1
        assert data == audio.getvalue()
        return "Original recognized answer"

    def evaluate(self, attempt):
        calls["evaluate"] += 1
        assert attempt["answer"] == "Corrected final answer"
        return evaluation

    monkeypatch.setattr(CoachAI, "transcribe", transcribe)
    monkeypatch.setattr(CoachAI, "evaluate", evaluate)
    log_in(at, "speaker")
    next(r for r in at.radio if r.label == "Answer using").set_value("Record").run()
    assert calls == {"transcribe": 0, "evaluate": 0}
    click(at, "Transcribe recording")
    assert calls == {"transcribe": 1, "evaluate": 0}
    assert repo.list_attempts(admin, uid) == []
    answer = next(t for t in at.text_area if t.label == "Your answer / editable transcript")
    assert answer.value == "Original recognized answer"
    answer.set_value("Corrected final answer").run()
    click(at, "Transcribe recording")
    assert next(t for t in at.text_area if t.label == "Your answer / editable transcript").value == "Corrected final answer"
    assert calls["transcribe"] == 1
    click(at, "Submit answer")
    saved = repo.list_attempts(admin, uid)[0]
    assert calls == {"transcribe": 1, "evaluate": 1}
    assert saved["answer"] == "Corrected final answer"
    assert saved["raw_transcript"] == "Original recognized answer"


def test_progress_chart_and_analysis_are_saved_once_per_click(app, monkeypatch, evaluation):
    at, repo = app
    admin = repo.authenticate("coach_admin", PASSWORD)
    uid = repo.create_user(admin, "learner", PASSWORD, "Learner")
    monkeypatch.setattr(CoachAI, "evaluate", lambda *args: evaluation)
    analysis = {"summary": "Your openings are clear.", "strengths": ["Clear opening"],
                "recurring_weaknesses": ["Limited evidence"], "trends": ["Similar scores across two attempts"],
                "recommendations": ["Include an outcome"], "next_training_focus": ["Evidence"], "limitations": "Only two attempts"}
    monkeypatch.setattr(CoachAI, "analyze", lambda *args: analysis)
    log_in(at, "learner")
    for i in range(2):
        next(t for t in at.text_area if t.label == "Your answer / editable transcript").set_value(f"Answer {i} with a clear decision.").run()
        click(at, "Submit answer")
        click(at, "Next question")
    at.radio(key="page").set_value("Progress").run()
    assert not at.exception
    click(at, "Generate and save analysis")
    assert repo.list_analyses(admin, uid)[0]["analysis"] == analysis
    at.run()
    assert len(repo.list_analyses(admin, uid)) == 1
    assert not at.exception
