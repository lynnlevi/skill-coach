"""Run with: streamlit run app.py"""
from copy import deepcopy
from datetime import timezone
import hashlib
import json
import time
from uuid import uuid4

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from coaching.ai import CoachAI, AIError
from coaching.db import build_engine
from coaching.domain import MAX_ANSWER_CHARS, MAX_AUDIO_BYTES, validate_config
from coaching.repository import Repository
from coaching.security import AccessDenied
from coaching.service import evaluate_attempt
from coaching.settings import Settings

st.set_page_config(page_title="Coaching Space", page_icon="🌱", layout="centered")


@st.cache_resource
def repository(database_url):
    return Repository(build_engine(database_url))


@st.cache_resource
def initialize(_repo, _settings, database_url):
    _repo.initialize(_settings)
    return True


def clear_workspace():
    for key in list(st.session_state):
        if key not in {"principal", "login_time", "workspace_id", "admin_page"}:
            del st.session_state[key]


def logout():
    st.session_state.clear()
    st.rerun()


def date_label(value):
    if value is None:
        return "Never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")


def show_evaluation(evaluation):
    st.metric("Overall score", f"{evaluation['overall_score']:.1f} / 10")
    st.write(evaluation["summary"])
    st.dataframe([
        {"Criterion": d["name"], "Score / 10": d["score"], "Feedback": d["feedback"]}
        for d in evaluation["dimension_scores"]
    ], hide_index=True, width="stretch")
    left, right = st.columns(2)
    with left:
        st.markdown("**What worked**")
        for item in evaluation["strengths"]:
            st.write(f"• {item}")
    with right:
        st.markdown("**What to improve**")
        for item in evaluation["improvements"]:
            st.write(f"• {item}")
    st.markdown("**Try this approach**")
    st.write(evaluation["suggested_approach"])


def login(repo):
    st.title("Your space to grow")
    st.write("One question. A thoughtful answer. A clearer next step.")
    if not repo.has_users():
        st.info("First-time setup: set BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD in your environment or Streamlit secrets, then restart the app. See README.md.")
        st.stop()
    with st.form("login", clear_on_submit=True):
        username = st.text_input("Username", max_chars=64)
        password = st.text_input("Password", type="password", max_chars=128)
        submitted = st.form_submit_button("Log in", type="primary", width="stretch")
    if submitted:
        # Small session cooldown also covers nonexistent usernames.
        if time.time() < st.session_state.get("login_retry_at", 0):
            st.warning("Please wait a moment before trying again.")
        else:
            principal = repo.authenticate(username, password)
            if principal:
                st.session_state.clear()
                st.session_state.principal = principal
                st.session_state.login_time = time.time()
                st.rerun()
            st.session_state.login_retry_at = time.time() + 2
            st.error("Unable to log in. Check your credentials or contact your coach. After five failed attempts, wait 15 minutes.")
    st.caption("Accounts are created by your coach. Contact them for access or a password reset.")
    st.stop()


def admin_page(repo, principal):
    st.title("Coach dashboard")
    st.write("Manage learners and open their coaching spaces.")
    with st.expander("Create a learner account"):
        with st.form("create_user", clear_on_submit=True):
            name = st.text_input("Name", max_chars=120)
            username = st.text_input("New username", max_chars=64)
            password = st.text_input("Initial password", type="password", max_chars=128,
                                     help="Use a unique password of at least 12 characters.")
            if st.form_submit_button("Create account", type="primary"):
                repo.create_user(principal, username, password, name)
                st.success("Account created. Share the username and password with the learner privately.")
    for user in repo.list_users(principal):
        with st.container(border=True):
            st.subheader(user["name"])
            st.caption(f"@{user['username']} · {user['role']} · {'Active' if user['active'] else 'Disabled'}")
            st.write(f"{user['attempt_count'] or 0} attempts · Last practice: {date_label(user['last_practice'])}")
            left, right = st.columns(2)
            if left.button("Open workspace", key=f"open_{user['id']}", width="stretch"):
                st.session_state.workspace_id = user["id"]
                st.session_state.admin_page = False
                clear_workspace()
                st.rerun()
            if user["role"] != "admin":
                if right.button("Disable user" if user["active"] else "Enable user", key=f"active_{user['id']}", width="stretch"):
                    repo.set_active(principal, user["id"], not user["active"])
                    st.rerun()
            with st.expander("Reset password"):
                with st.form(f"reset_{user['id']}", clear_on_submit=True):
                    new_password = st.text_input("New password", type="password", max_chars=128, key=f"pw_{user['id']}")
                    if st.form_submit_button("Save new password"):
                        repo.reset_password(principal, user["id"], new_password)
                        if user["id"] == principal.user_id:
                            logout()
                        st.success("Password changed. Existing sessions have been revoked.")
    with st.expander("Add a practice question"):
        st.caption("Questions in the bank are available to every workspace.")
        with st.form("add_question", clear_on_submit=True):
            question = st.text_area("Question", max_chars=2000)
            category = st.text_input("Category", max_chars=100)
            if st.form_submit_button("Add question"):
                repo.add_question(principal, question, category)
                st.success("Question added to the bank.")


def new_draft(question, config, reason=""):
    return {"submission_id": str(uuid4()), "question": question, "config": validate_config(config),
            "answer": "", "raw_transcript": None, "audio_hash": None,
            "transcription_model": None, "attempt_id": None, "reason": reason}


def remember_answer(key):
    st.session_state.draft["answer"] = st.session_state[key]


def practice(repo, principal, user_id, settings):
    st.title("Practice")
    config = repo.get_config(principal, user_id)
    bank = repo.list_questions(principal)
    history = repo.list_attempts(principal, user_id, limit=10)
    if "draft" not in st.session_state:
        recent_ids = {a["question_id"] for a in history}
        question = next((q for q in bank if q["id"] not in recent_ids), bank[0])
        st.session_state.draft = new_draft(question, config)
    draft = st.session_state.draft
    st.caption(f"Training goal: {draft['config']['training_goal']}")
    if draft["config"] != validate_config(config):
        st.info("Configuration has changed. This question keeps its original rubric; the next question will use the new one.")
    with st.container(border=True):
        st.caption(draft["question"]["category"])
        st.subheader(draft["question"]["text"])
        if draft["reason"]:
            st.caption(draft["reason"])
    with st.expander("Rubric for this question"):
        st.dataframe(draft["config"]["rubric"], hide_index=True, width="stretch")
    if draft["attempt_id"]:
        attempt = repo.get_attempt(principal, user_id, draft["attempt_id"])
        st.markdown("**Your submitted answer**")
        st.write(attempt["answer"])
        if attempt["status"] == "completed":
            st.success("Saved to Progress")
            show_evaluation(attempt["evaluation"])
        else:
            st.info("Your answer is saved in Progress. Evaluation is not complete yet.")
            if attempt["error_message"]:
                st.warning(attempt["error_message"])
            if st.button("Retry evaluation", type="primary"):
                with st.spinner("Evaluating your saved answer…"):
                    evaluate_attempt(repo, principal, user_id, attempt["id"], settings)
                st.rerun()
    else:
        st.write("Type your answer, or record it and review the transcript before submitting.")
        st.caption("Transcription sends your recording to OpenAI. Submission sends the final text and coaching profile. Audio is not stored in the app's database.")
        mode = st.radio("Answer using", ["Type", "Record"], horizontal=True, key=f"mode_{draft['submission_id']}")
        answer_key = f"answer_{draft['submission_id']}"
        if mode == "Record":
            audio = st.audio_input("Record your answer", sample_rate=16000, key=f"audio_{draft['submission_id']}")
            if st.button("Transcribe recording", disabled=audio is None):
                if audio.size > MAX_AUDIO_BYTES:
                    raise AIError("Please record a shorter answer (under 24 MB).")
                repo.workspace_user(principal, user_id)
                content = audio.getvalue()
                digest = hashlib.sha256(content).hexdigest()
                if digest == draft["audio_hash"]:
                    st.info("This recording is already transcribed. Your edits are preserved below.")
                else:
                    with st.spinner("Transcribing…"):
                        transcript = CoachAI(settings).transcribe(content)
                    draft.update(answer=transcript, raw_transcript=transcript, audio_hash=digest,
                                 transcription_model=settings.transcription_model)
                    st.session_state[answer_key] = transcript
            st.caption("A new transcription replaces the answer below. Recording alone does not submit anything.")
        if answer_key not in st.session_state:
            st.session_state[answer_key] = draft["answer"]
        st.text_area("Your answer / editable transcript", key=answer_key, height=230,
                     on_change=remember_answer, args=(answer_key,),
                     help=f"Review and edit before submitting. Maximum {MAX_ANSWER_CHARS:,} characters.")
        draft["answer"] = st.session_state[answer_key]
        if st.button("Submit answer", type="primary", disabled=not draft["answer"].strip()):
            # Commit the official answer before making the potentially slow API request.
            draft["attempt_id"] = repo.create_attempt(
                principal, user_id, draft["submission_id"], draft["question"]["id"], draft["answer"],
                draft["config"], settings.model, draft["raw_transcript"], draft["transcription_model"],
            )
            try:
                with st.spinner("Evaluating your answer…"):
                    evaluate_attempt(repo, principal, user_id, draft["attempt_id"], settings)
            except AIError:
                # Render the saved attempt and its safe error message immediately.
                st.rerun()
            st.rerun()
    st.divider()
    left, right = st.columns(2)
    has_unsaved_answer = bool(draft["answer"].strip()) and not draft["attempt_id"]
    if has_unsaved_answer:
        allow_replace = st.checkbox("Discard this draft when changing questions", key=f"discard_{draft['submission_id']}")
    else:
        allow_replace = True
    if left.button("Next question", disabled=not allow_replace, width="stretch"):
        index = next((i for i, q in enumerate(bank) if q["id"] == draft["question"]["id"]), -1)
        st.session_state.draft = new_draft(bank[(index + 1) % len(bank)], config)
        st.rerun()
    if right.button("Choose with AI", disabled=not allow_replace, width="stretch"):
        with st.spinner("Choosing a question for your goals…"):
            question, reason = CoachAI(settings).choose_question(validate_config(config), bank, history)
        st.session_state.draft = new_draft(question, config, reason)
        st.rerun()


def progress(repo, principal, user_id, settings):
    st.title("Progress")
    history = repo.list_attempts(principal, user_id)
    completed = [a for a in history if a["status"] == "completed"]
    columns = st.columns(3)
    columns[0].metric("Attempts", len(history))
    columns[1].metric("Completed", len(completed))
    columns[2].metric("Recent average", f"{sum(a['overall_score'] for a in completed[:5]) / len(completed[:5]):.1f} / 10" if completed else "—")
    st.caption("Recent average uses up to five completed attempts. Scores may not be comparable if the rubric or goal changed.")
    if len(completed) >= 2:
        chart = [{"Attempt": a["id"], "Score": a["overall_score"]} for a in reversed(completed)]
        st.line_chart(chart, x="Attempt", y="Score", y_label="Score / 10")
    if not history:
        st.info("Your practice history will appear here after your first submission.")
    st.subheader("Patterns and trends")
    st.caption("Analyze up to your latest 30 completed attempts. Each analysis is saved as a dated snapshot.")
    if st.button("Generate and save analysis", disabled=len(completed) < 2, type="primary"):
        config = validate_config(repo.get_config(principal, user_id))
        selected = completed[:30]
        with st.spinner("Reviewing your progress…"):
            analysis = CoachAI(settings).analyze(config, selected)
            repo.save_analysis(principal, user_id, [a["id"] for a in selected], config, analysis, settings.model)
        st.success("Analysis saved.")
    if len(completed) < 2:
        st.caption("Complete at least two evaluations to analyze patterns.")
    for saved in repo.list_analyses(principal, user_id):
        with st.expander(f"Analysis · {date_label(saved['created_at'])} · {len(saved['attempt_ids'])} attempts"):
            st.caption(f"Covers {date_label(saved['attempt_start'])} to {date_label(saved['attempt_end'])}")
            analysis = saved["analysis"]
            st.write(analysis["summary"])
            for field, label in [("strengths", "Strengths"), ("recurring_weaknesses", "Recurring challenges"),
                                 ("trends", "Changes over time"), ("recommendations", "Recommendations"),
                                 ("next_training_focus", "Next training focus")]:
                st.markdown(f"**{label}**")
                for item in analysis[field]:
                    st.write(f"• {item}")
            st.caption(analysis["limitations"])
    st.subheader("Practice history")
    order = st.radio("Chronological order", ["Newest first", "Oldest first"], horizontal=True)
    display = history if order == "Newest first" else list(reversed(history))
    for attempt in display:
        score = f"{attempt['overall_score']:.1f}/10" if attempt["status"] == "completed" else attempt["status"]
        with st.expander(f"{date_label(attempt['created_at'])} · #{attempt['id']} · {score}"):
            st.markdown("**Question**")
            st.write(attempt["question_text"])
            st.markdown("**Final answer**")
            st.write(attempt["answer"])
            if attempt["raw_transcript"] is not None:
                st.markdown("**Original transcript**")
                st.write(attempt["raw_transcript"])
            if attempt["status"] == "completed":
                show_evaluation(attempt["evaluation"])
            else:
                st.warning(attempt["error_message"] or "Evaluation is pending or running.")
                if st.button("Retry evaluation", key=f"retry_{attempt['id']}"):
                    with st.spinner("Evaluating…"):
                        evaluate_attempt(repo, principal, user_id, attempt["id"], settings)
                    st.rerun()
            st.caption(f"Submitted by account #{attempt['created_by']} · {attempt['model']} · {attempt['prompt_version']}")
            st.markdown("**Configuration used**")
            st.json(attempt["config_snapshot"], expanded=False)
    if history:
        st.download_button("Download my history (JSON)", data=json.dumps(history, default=str, ensure_ascii=False, indent=2),
                           file_name="coaching-history.json", mime="application/json")


def configuration(repo, principal, user_id):
    st.title("Configuration")
    st.write("Set the direction for your practice. Both you and your coach can update these settings.")
    config = repo.get_config(principal, user_id)
    with st.form("configuration"):
        goal = st.text_area("Training goal", value=config["training_goal"], max_chars=2000)
        context = st.text_area("Context", value=config["context"], height=130, max_chars=8000,
                               help="Relevant experience, target situations, and current challenges.")
        st.markdown("**Evaluation rubric**")
        st.caption("Add or remove criteria. Weights must total 100%; each criterion is scored out of 10.")
        rubric = st.data_editor(deepcopy(config["rubric"]), num_rows="dynamic", hide_index=True,
                                width="stretch", key=f"rubric_{user_id}", column_config={
                                    "name": st.column_config.TextColumn("Criterion", required=True),
                                    "weight": st.column_config.NumberColumn("Weight (%)", min_value=0.1, max_value=100, required=True),
                                    "description": st.column_config.TextColumn("What good looks like"),
                                })
        instructions = st.text_area("Coaching instructions", value=config["coaching_instructions"], height=130, max_chars=4000)
        if st.form_submit_button("Save configuration", type="primary"):
            repo.save_config(principal, user_id, {"training_goal": goal, "context": context,
                                                "rubric": rubric, "coaching_instructions": instructions})
            st.success("Saved. These settings apply to your next question. Existing attempts keep their original rubric.")


def main():
    try:
        secrets = dict(st.secrets)
    except FileNotFoundError:
        secrets = {}
    settings = Settings.load(secrets)
    repo = repository(settings.database_url)
    initialize(repo, settings, settings.database_url)
    principal = st.session_state.get("principal")
    if not principal:
        login(repo)
    if time.time() - st.session_state.get("login_time", 0) > settings.session_hours * 3600:
        logout()
    actor = repo.identity(principal)
    with st.sidebar:
        st.title("🌱 Coaching Space")
        st.caption(f"Signed in as {actor['name']}")
        if st.button("Log out", width="stretch"):
            logout()
        if actor["role"] == "admin":
            if st.button("Coach dashboard", width="stretch"):
                st.session_state.admin_page = True
                clear_workspace()
                st.rerun()
    if actor["role"] == "admin" and st.session_state.get("admin_page", True):
        admin_page(repo, principal)
        return
    user_id = st.session_state.get("workspace_id", actor["id"]) if actor["role"] == "admin" else actor["id"]
    workspace = repo.workspace_user(principal, user_id)
    if actor["role"] == "admin":
        st.info(f"Coach view · {workspace['name']} (@{workspace['username']}). Changes and practice submissions will be saved in this workspace under your account ID.")
    if not workspace["active"]:
        st.warning("This learner's login is disabled. Their saved workspace remains available to you.")
    page = st.sidebar.radio("Workspace", ["Practice", "Progress", "Configuration"], key="page")
    if page == "Practice":
        practice(repo, principal, user_id, settings)
    elif page == "Progress":
        progress(repo, principal, user_id, settings)
    else:
        configuration(repo, principal, user_id)


try:
    main()
except AccessDenied as exc:
    st.session_state.clear()
    st.error(str(exc))
    if st.button("Return to login"):
        st.rerun()
except (ValueError, AIError) as exc:
    st.error(str(exc))
except SQLAlchemyError:
    st.error("The database could not complete this operation. Check the database connection and retry. A submitted answer may already be saved in Progress.")
