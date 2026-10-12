"""All workspace operations check the signed-in actor against fresh database state."""
from copy import deepcopy
import time
from uuid import UUID, uuid4

from sqlalchemy import select, insert, update, func, or_, and_, text
from sqlalchemy.exc import IntegrityError

from .db import metadata, users, configs, questions, attempts, analyses, utcnow
from .domain import DEFAULT_CONFIG, PROMPT_VERSION, MAX_ANSWER_CHARS, Evaluation, score_evaluation, validate_config
from .security import (
    Principal, AccessDenied, hash_password, verify_password,
    validate_username, normalize_username, hasher,
)


class Repository:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self, settings):
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                # Serialize first startup across deployment workers.
                conn.execute(text("SELECT pg_advisory_xact_lock(839173041)"))
            metadata.create_all(conn)
            if not conn.scalar(select(func.count()).select_from(users)) and settings.admin_password:
                self._insert_user(conn, settings.admin_username, settings.admin_password, settings.admin_name, "admin")

    @staticmethod
    def _insert_user(conn, username, password, name, role):
        username = validate_username(username)
        if not name.strip() or len(name.strip()) > 120:
            raise ValueError("Name must be 1–120 characters long.")
        user_id = conn.execute(insert(users).values(
            username=username, password_hash=hash_password(password), name=name.strip(), role=role,
        )).inserted_primary_key[0]
        conn.execute(insert(configs).values(user_id=user_id, updated_by=user_id, **deepcopy(DEFAULT_CONFIG)))
        return user_id

    @staticmethod
    def _actor(conn, principal: Principal):
        user = conn.execute(select(users).where(users.c.id == principal.user_id)).mappings().first()
        if not user or not user["active"] or user["session_version"] != principal.session_version:
            raise AccessDenied("Your session has ended. Please log in again.")
        return user

    def _authorize(self, conn, principal, user_id=None, admin=False):
        actor = self._actor(conn, principal)
        if admin and actor["role"] != "admin":
            raise AccessDenied("This action is available only to the coach.")
        if user_id is not None:
            if actor["role"] != "admin" and actor["id"] != user_id:
                raise AccessDenied("You cannot open this workspace.")
            if not conn.scalar(select(users.c.id).where(users.c.id == user_id)):
                raise AccessDenied("This workspace is unavailable.")
        return actor

    def has_users(self):
        with self.engine.connect() as conn:
            return bool(conn.scalar(select(func.count()).select_from(users)))

    def authenticate(self, username, password):
        username = normalize_username(username)
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(users.c.username == username).with_for_update()).mappings().first()
            valid = verify_password(row["password_hash"] if row else None, password)
            if not row:
                return None
            now = time.time()
            if row["locked_until"] > now or not row["active"]:
                return None
            if not valid:
                count = (0 if row["locked_until"] else row["failed_logins"]) + 1
                conn.execute(update(users).where(users.c.id == row["id"]).values(
                    failed_logins=count, locked_until=now + 900 if count >= 5 else 0,
                ))
                return None
            changes = {"failed_logins": 0, "locked_until": 0}
            if hasher.check_needs_rehash(row["password_hash"]):
                changes["password_hash"] = hash_password(password)
            conn.execute(update(users).where(users.c.id == row["id"]).values(**changes))
            return Principal(row["id"], row["session_version"])

    def identity(self, principal):
        with self.engine.connect() as conn:
            actor = self._actor(conn, principal)
            return {k: actor[k] for k in ("id", "username", "name", "role", "active")}

    def workspace_user(self, principal, user_id):
        with self.engine.connect() as conn:
            self._authorize(conn, principal, user_id)
            row = conn.execute(select(users.c.id, users.c.username, users.c.name, users.c.role, users.c.active)
                               .where(users.c.id == user_id)).mappings().one()
            return dict(row)

    def list_users(self, principal):
        with self.engine.connect() as conn:
            self._authorize(conn, principal, admin=True)
            activity = select(attempts.c.user_id, func.count().label("attempt_count"),
                              func.max(attempts.c.created_at).label("last_practice")).group_by(attempts.c.user_id).subquery()
            awaiting = select(attempts.c.user_id, func.count().label("awaiting_count")).where(
                attempts.c.status == "completed", attempts.c.released_at.is_(None),
            ).group_by(attempts.c.user_id).subquery()
            stmt = (select(users.c.id, users.c.username, users.c.name, users.c.role, users.c.active,
                           users.c.review_required, activity.c.attempt_count, activity.c.last_practice,
                           awaiting.c.awaiting_count)
                    .outerjoin(activity, users.c.id == activity.c.user_id)
                    .outerjoin(awaiting, users.c.id == awaiting.c.user_id))
            return [dict(r) for r in conn.execute(stmt.order_by(users.c.name)).mappings()]

    def create_user(self, principal, username, password, name):
        try:
            with self.engine.begin() as conn:
                self._authorize(conn, principal, admin=True)
                return self._insert_user(conn, username, password, name, "user")
        except IntegrityError:
            raise ValueError("That username is already taken.") from None

    def set_active(self, principal, user_id, active):
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id, admin=True)
            target = conn.execute(select(users).where(users.c.id == user_id)).mappings().one()
            if target["role"] == "admin":
                raise ValueError("Admin accounts cannot be disabled in this MVP.")
            conn.execute(update(users).where(users.c.id == user_id).values(
                active=active, session_version=users.c.session_version + 1,
            ))

    def reset_password(self, principal, user_id, password):
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id, admin=True)
            self._reset_password(conn, user_id, password)

    @staticmethod
    def _reset_password(conn, user_id, password):
        conn.execute(update(users).where(users.c.id == user_id).values(
            password_hash=hash_password(password), session_version=users.c.session_version + 1,
            failed_logins=0, locked_until=0,
        ))

    def get_config(self, principal, user_id):
        with self.engine.connect() as conn:
            self._authorize(conn, principal, user_id)
            return dict(conn.execute(select(configs).where(configs.c.user_id == user_id)).mappings().one())

    def save_config(self, principal, user_id, config):
        config = validate_config(config)
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id)
            conn.execute(update(configs).where(configs.c.user_id == user_id).values(
                **config, updated_at=utcnow(), updated_by=principal.user_id,
            ))

    def list_questions(self, principal, user_id):
        with self.engine.connect() as conn:
            self._authorize(conn, principal, user_id)
            return [dict(r) for r in conn.execute(select(questions).where(
                questions.c.user_id == user_id, questions.c.active.is_(True),
            ).order_by(questions.c.id)).mappings()]

    def add_question(self, principal, user_id, question, category):
        if not 5 <= len(question.strip()) <= 2000 or not 1 <= len(category.strip()) <= 100:
            raise ValueError("Enter a question of 5–2,000 characters and a category of 1–100 characters.")
        try:
            with self.engine.begin() as conn:
                self._authorize(conn, principal, user_id, admin=True)
                conn.execute(insert(questions).values(user_id=user_id, text=question.strip(), category=category.strip()))
        except IntegrityError:
            raise ValueError("That question is already in this workspace's bank.") from None

    @staticmethod
    def _present(row, review_required, is_coach):
        """Attach what this viewer may see. The coach sees the AI original too."""
        data = dict(row)
        if data["released_at"] is not None:
            data["visible_evaluation"] = data["released_evaluation"]
            data["visible_score"] = data["released_score"]
        elif not review_required:
            data["visible_evaluation"] = data["evaluation"]
            data["visible_score"] = data["overall_score"]
        else:
            data["visible_evaluation"] = None
            data["visible_score"] = None
        data["awaiting_review"] = bool(
            review_required and data["released_at"] is None and data["status"] == "completed")
        if not is_coach:
            # A learner never receives unreleased feedback, not even in the page source.
            data["evaluation"] = data["visible_evaluation"]
            data["overall_score"] = data["visible_score"]
            data["released_evaluation"] = None
            data["released_score"] = None
        return data

    def set_review_required(self, principal, user_id, required):
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id, admin=True)
            conn.execute(update(users).where(users.c.id == user_id).values(review_required=bool(required)))

    def save_review(self, principal, user_id, attempt_id, evaluation, note="", release=False):
        """Store the coach's edited evaluation; release makes it visible to the learner."""
        note = (note or "").strip()
        if len(note) > 4000:
            raise ValueError("Keep the note to 4,000 characters or fewer.")
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id, admin=True)
            row = conn.execute(select(attempts).where(
                attempts.c.id == attempt_id, attempts.c.user_id == user_id)).mappings().first()
            if not row:
                raise AccessDenied("This attempt is unavailable.")
            if row["status"] != "completed":
                raise ValueError("Only a completed evaluation can be reviewed.")
            scored = score_evaluation(Evaluation(**evaluation), row["config_snapshot"])
            changes = {"released_evaluation": scored, "released_score": scored["overall_score"],
                       "coach_note": note or None}
            if release:
                changes.update(released_at=utcnow(), released_by=principal.user_id)
            conn.execute(update(attempts).where(attempts.c.id == attempt_id).values(**changes))
            return scored

    def list_attempts(self, principal, user_id, limit=None, completed_only=False):
        with self.engine.connect() as conn:
            actor = self._authorize(conn, principal, user_id)
            review_required = bool(conn.scalar(select(users.c.review_required).where(users.c.id == user_id)))
            stmt = select(attempts).where(attempts.c.user_id == user_id)
            if completed_only:
                stmt = stmt.where(attempts.c.status == "completed")
            stmt = stmt.order_by(attempts.c.created_at.desc(), attempts.c.id.desc())
            if limit:
                stmt = stmt.limit(limit)
            is_coach = actor["role"] == "admin"
            return [self._present(r, review_required, is_coach) for r in conn.execute(stmt).mappings()]

    def get_attempt(self, principal, user_id, attempt_id):
        with self.engine.connect() as conn:
            actor = self._authorize(conn, principal, user_id)
            row = conn.execute(select(attempts).where(attempts.c.id == attempt_id,
                                                      attempts.c.user_id == user_id)).mappings().first()
            if not row:
                raise AccessDenied("This attempt is unavailable.")
            review_required = bool(conn.scalar(select(users.c.review_required).where(users.c.id == user_id)))
            return self._present(row, review_required, actor["role"] == "admin")

    def create_attempt(self, principal, user_id, submission_id, question_id, answer,
                       config, model, raw_transcript=None, transcription_model=None):
        answer = answer.strip()
        if not answer or len(answer) > MAX_ANSWER_CHARS:
            raise ValueError(f"Enter an answer of 1–{MAX_ANSWER_CHARS:,} characters.")
        submission_id = str(UUID(submission_id))
        snapshot = validate_config(config)
        try:
            with self.engine.begin() as conn:
                self._authorize(conn, principal, user_id)
                existing = conn.execute(select(attempts).where(attempts.c.submission_id == submission_id)).mappings().first()
                if existing:
                    if existing["user_id"] != user_id or existing["created_by"] != principal.user_id:
                        raise AccessDenied("This submission is unavailable.")
                    return existing["id"]
                question = conn.execute(select(questions).where(
                    questions.c.id == question_id, questions.c.user_id == user_id,
                )).mappings().first()
                if not question:
                    raise AccessDenied("This question is unavailable in this workspace.")
                return conn.execute(insert(attempts).values(
                    submission_id=submission_id, user_id=user_id, created_by=principal.user_id,
                    question_id=question_id, question_text=question["text"], answer=answer,
                    answer_source="audio" if raw_transcript is not None else "typed",
                    raw_transcript=raw_transcript, transcription_model=transcription_model,
                    config_snapshot=snapshot, model=model, prompt_version=PROMPT_VERSION,
                )).inserted_primary_key[0]
        except IntegrityError:
            # A concurrent duplicate submission may have won the unique constraint.
            with self.engine.connect() as conn:
                self._authorize(conn, principal, user_id)
                row = conn.execute(select(attempts.c.id).where(
                    attempts.c.submission_id == submission_id, attempts.c.user_id == user_id,
                    attempts.c.created_by == principal.user_id,
                )).first()
                if row:
                    return row[0]
            raise

    def claim_evaluation(self, principal, user_id, attempt_id):
        token = str(uuid4())
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id)
            result = conn.execute(update(attempts).where(
                attempts.c.id == attempt_id, attempts.c.user_id == user_id,
                or_(attempts.c.status.in_(["pending", "failed"]), and_(
                    attempts.c.status == "evaluating", attempts.c.evaluation_lease_until < time.time())),
            ).values(status="evaluating", evaluation_token=token,
                     evaluation_lease_until=time.time() + 180, error_message=None))
            return token if result.rowcount else None

    def finish_evaluation(self, principal, user_id, attempt_id, token, evaluation=None, error=None):
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id)
            conn.execute(update(attempts).where(
                attempts.c.id == attempt_id, attempts.c.user_id == user_id,
                attempts.c.evaluation_token == token, attempts.c.status == "evaluating",
            ).values(
                status="completed" if evaluation else "failed", evaluation=evaluation,
                overall_score=evaluation["overall_score"] if evaluation else None,
                error_message=error, evaluated_at=utcnow() if evaluation else None,
                evaluation_token=None, evaluation_lease_until=0,
            ))

    def list_analyses(self, principal, user_id):
        with self.engine.connect() as conn:
            self._authorize(conn, principal, user_id)
            return [dict(r) for r in conn.execute(select(analyses).where(analyses.c.user_id == user_id)
                                                .order_by(analyses.c.created_at.desc(), analyses.c.id.desc())).mappings()]

    def save_analysis(self, principal, user_id, attempt_ids, config, analysis, model):
        with self.engine.begin() as conn:
            self._authorize(conn, principal, user_id)
            rows = conn.execute(select(attempts).where(
                attempts.c.user_id == user_id, attempts.c.id.in_(attempt_ids), attempts.c.status == "completed",
            ).order_by(attempts.c.created_at)).mappings().all()
            if len(rows) != len(set(attempt_ids)) or len(rows) < 2:
                raise ValueError("Analysis needs at least two completed attempts in this workspace.")
            return conn.execute(insert(analyses).values(
                user_id=user_id, created_by=principal.user_id, attempt_ids=[r["id"] for r in rows],
                attempt_start=rows[0]["created_at"], attempt_end=rows[-1]["created_at"],
                config_snapshot=validate_config(config), analysis=analysis,
                model=model, prompt_version=PROMPT_VERSION,
            )).inserted_primary_key[0]
