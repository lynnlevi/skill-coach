from copy import deepcopy
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from coaching.ai import AIError
from coaching.db import users, attempts
from coaching.domain import DEFAULT_CONFIG, validate_config
from coaching.repository import Repository
from coaching.security import AccessDenied, hash_password, verify_password
from coaching.service import evaluate_attempt
from coaching.settings import Settings
from conftest import PASSWORD


def create_attempt(repo, actor, uid, submission_id=None, **kwargs):
    return repo.create_attempt(actor, uid, submission_id or str(uuid4()), 1, "I chose the smaller project because it solved the main customer problem.",
                               deepcopy(DEFAULT_CONFIG), "gpt-4.1-mini", **kwargs)


def test_passwords_are_salted_and_verified():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, PASSWORD)
    assert not verify_password(first, "wrong")
    assert not verify_password(None, PASSWORD)
    assert not verify_password("broken hash", PASSWORD)
    with pytest.raises(ValueError):
        hash_password("short")


def test_bootstrap_is_idempotent_and_never_overwrites(repo):
    repo.initialize(Settings(database_url="unused", admin_password="different-password-456"))
    principal = repo.authenticate(" COACH_ADMIN ", PASSWORD)
    assert principal
    assert len(repo.list_users(principal)) == 1
    assert len(repo.list_questions(principal)) == 10
    assert repo.authenticate("coach_admin", "different-password-456") is None
    with repo.engine.connect() as conn:
        assert conn.scalar(select(users.c.password_hash)).startswith("$argon2id$")


def test_lockout_is_saved_and_password_reset_revokes_session(repo, accounts):
    admin, alice, _, alice_id, _ = accounts
    for _ in range(5):
        assert repo.authenticate("alice", "wrong") is None
    assert repo.authenticate("alice", PASSWORD) is None
    with repo.engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == alice_id).values(locked_until=time.time() - 1))
    assert repo.authenticate("alice", PASSWORD)
    repo.reset_password(admin, alice_id, "new-long-password-456")
    with pytest.raises(AccessDenied):
        repo.identity(alice)
    assert repo.authenticate("alice", PASSWORD) is None
    assert repo.authenticate("alice", "new-long-password-456")


def test_user_isolation_and_admin_access(repo, accounts):
    admin, alice, bob, alice_id, bob_id = accounts
    attempt_id = create_attempt(repo, alice, alice_id)
    assert repo.get_attempt(admin, alice_id, attempt_id)["answer"]
    assert repo.get_attempt(alice, alice_id, attempt_id)["id"] == attempt_id
    assert repo.list_attempts(bob, bob_id) == []
    operations = [
        lambda: repo.get_config(bob, alice_id),
        lambda: repo.save_config(bob, alice_id, deepcopy(DEFAULT_CONFIG)),
        lambda: repo.list_attempts(bob, alice_id),
        lambda: repo.get_attempt(bob, bob_id, attempt_id),
        lambda: repo.list_analyses(bob, alice_id),
        lambda: repo.create_user(bob, "eve", PASSWORD, "Eve"),
        lambda: repo.list_users(bob),
        lambda: repo.set_active(bob, alice_id, False),
        lambda: repo.reset_password(bob, alice_id, PASSWORD),
        lambda: create_attempt(repo, bob, alice_id),
    ]
    for operation in operations:
        with pytest.raises(AccessDenied):
            operation()


def test_disable_revokes_existing_sessions_and_preserves_history(repo, accounts):
    admin, alice, _, alice_id, _ = accounts
    aid = create_attempt(repo, alice, alice_id)
    repo.set_active(admin, alice_id, False)
    assert repo.authenticate("alice", PASSWORD) is None
    with pytest.raises(AccessDenied):
        repo.list_attempts(alice, alice_id)
    assert repo.get_attempt(admin, alice_id, aid)
    repo.set_active(admin, alice_id, True)
    with pytest.raises(AccessDenied):
        repo.identity(alice)
    assert repo.authenticate("alice", PASSWORD)
    with pytest.raises(ValueError):
        repo.set_active(admin, admin.user_id, False)


def test_unique_normalized_username(repo, accounts):
    admin = accounts[0]
    with pytest.raises(ValueError, match="already taken"):
        repo.create_user(admin, " ALICE ", PASSWORD, "Someone else")
    with pytest.raises(ValueError):
        repo.create_user(admin, "a'; DROP TABLE users;--", PASSWORD, "Someone")


def test_attempt_snapshot_raw_transcript_and_duplicate_submission(repo, accounts):
    admin, alice, _, uid, _ = accounts
    token = str(uuid4())
    aid = create_attempt(repo, alice, uid, token, raw_transcript="raw recognition", transcription_model="gpt-4o-mini-transcribe")
    assert create_attempt(repo, alice, uid, token) == aid
    config = deepcopy(DEFAULT_CONFIG)
    config["training_goal"] = "A different goal"
    repo.save_config(admin, uid, config)
    saved = repo.get_attempt(alice, uid, aid)
    assert saved["config_snapshot"]["training_goal"] == DEFAULT_CONFIG["training_goal"]
    assert saved["raw_transcript"] == "raw recognition"
    assert saved["answer_source"] == "audio"
    assert saved["created_by"] == uid
    # A fresh Repository instance sees committed data, not just session memory.
    assert len(Repository(repo.engine).list_attempts(alice, uid)) == 1
    coach_id = create_attempt(repo, admin, uid)
    assert repo.get_attempt(admin, uid, coach_id)["created_by"] == admin.user_id


def test_failed_evaluation_can_retry_without_losing_answer(repo, accounts, evaluation):
    _, alice, _, uid, _ = accounts
    aid = create_attempt(repo, alice, uid)
    settings = Settings(database_url="unused")

    class Failing:
        def evaluate(self, attempt):
            raise AIError("Temporary failure")

    with pytest.raises(AIError):
        evaluate_attempt(repo, alice, uid, aid, settings, Failing())
    saved = repo.get_attempt(alice, uid, aid)
    assert saved["status"] == "failed" and saved["answer"]

    class Passing:
        calls = 0

        def evaluate(self, attempt):
            self.calls += 1
            return evaluation

    ai = Passing()
    assert evaluate_attempt(repo, alice, uid, aid, settings, ai)["status"] == "completed"
    assert evaluate_attempt(repo, alice, uid, aid, settings, ai)["overall_score"] == 8
    assert ai.calls == 1
    assert len(repo.list_attempts(alice, uid)) == 1


def test_evaluation_lease_prevents_duplicate_work_and_stale_result(repo, accounts, evaluation):
    _, alice, _, uid, _ = accounts
    aid = create_attempt(repo, alice, uid)
    first = repo.claim_evaluation(alice, uid, aid)
    assert first
    assert repo.claim_evaluation(alice, uid, aid) is None
    with repo.engine.begin() as conn:
        conn.execute(update(attempts).where(attempts.c.id == aid).values(evaluation_lease_until=0))
    second = repo.claim_evaluation(alice, uid, aid)
    repo.finish_evaluation(alice, uid, aid, first, evaluation=evaluation)
    assert repo.get_attempt(alice, uid, aid)["status"] == "evaluating"
    repo.finish_evaluation(alice, uid, aid, second, evaluation=evaluation)
    assert repo.get_attempt(alice, uid, aid)["status"] == "completed"


def test_analysis_saves_exact_source_and_rejects_other_workspace(repo, accounts, evaluation):
    admin, alice, bob, uid, bob_id = accounts
    ids = [create_attempt(repo, alice, uid) for _ in range(2)]
    for aid in ids:
        token = repo.claim_evaluation(alice, uid, aid)
        repo.finish_evaluation(alice, uid, aid, token, evaluation=evaluation)
    repo.save_analysis(admin, uid, ids, DEFAULT_CONFIG, {"summary": "Improvement"}, "gpt-4.1-mini")
    saved = repo.list_analyses(alice, uid)[0]
    assert saved["attempt_ids"] == ids and saved["created_by"] == admin.user_id
    with pytest.raises(ValueError):
        repo.save_analysis(bob, bob_id, ids, DEFAULT_CONFIG, {"summary": "invalid"}, "model")


@pytest.mark.parametrize("weights", [[30, 20, 20, 20, 1], [float('nan'), 20, 20, 20, 10], [0, 30, 30, 30, 10]])
def test_rubric_rejects_invalid_weights(weights):
    config = deepcopy(DEFAULT_CONFIG)
    for row, weight in zip(config["rubric"], weights):
        row["weight"] = weight
    with pytest.raises(ValueError):
        validate_config(config)
