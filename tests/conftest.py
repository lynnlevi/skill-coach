from copy import deepcopy
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from coaching.db import build_engine
from coaching.domain import DEFAULT_CONFIG, Evaluation, score_evaluation
from coaching.repository import Repository
from coaching.settings import Settings

PASSWORD = "test-only-password-123!"


@pytest.fixture(params=["sqlite", "postgresql"] if os.getenv("TEST_POSTGRES_URL") else ["sqlite"])
def repo(request, tmp_path):
    cleanup = None
    if request.param == "postgresql":
        base = build_engine(os.environ["TEST_POSTGRES_URL"])
        schema = "coaching_test_" + uuid4().hex
        with base.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(base.url, connect_args={"options": f"-c search_path={schema}"}, hide_parameters=True)
        cleanup = (base, schema)
    else:
        engine = build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    settings = Settings(database_url=str(engine.url), admin_password=PASSWORD)
    repository = Repository(engine)
    repository.initialize(settings)
    yield repository
    engine.dispose()
    if cleanup:
        base, schema = cleanup
        with base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


@pytest.fixture
def accounts(repo):
    admin = repo.authenticate("coach_admin", PASSWORD)
    alice_id = repo.create_user(admin, "alice", PASSWORD, "Alice")
    bob_id = repo.create_user(admin, "bob", PASSWORD, "Bob")
    return admin, repo.authenticate("alice", PASSWORD), repo.authenticate("bob", PASSWORD), alice_id, bob_id


@pytest.fixture
def evaluation():
    return score_evaluation(Evaluation(
        summary="A clear answer with room for stronger evidence.",
        dimension_scores=[{"name": r["name"], "score": 8, "feedback": "A direct opening supports clarity."}
                          for r in DEFAULT_CONFIG["rubric"]],
        strengths=["Direct opening"], improvements=["Add a measurable outcome"],
        suggested_approach="Decision, reason, evidence, outcome.", tags=["communication"],
    ), deepcopy(DEFAULT_CONFIG))
