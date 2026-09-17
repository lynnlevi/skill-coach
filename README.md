# Coaching Space

A working Streamlit MVP for a coach and their learners. One app, one relational database, and the OpenAI Python SDK. No separate web server or background worker is required.

## What is included

- Manual username/password login with salted **Argon2id** hashes, admin/user roles, session expiry, and account lockout after five failed logins (15 minutes).
- Coach dashboard: create learners, disable/re-enable access, reset passwords, open any workspace, and add practice questions to a specific learner's own bank — written by hand or drafted by AI from a short prompt and reviewed before saving.
- **Practice:** one question at a time; type or record with `st.audio_input`; explicitly transcribe; review/edit the transcript; explicitly submit for structured evaluation.
- **Progress:** chronological question/answer/evaluation history, scores, original transcripts, rubric snapshots, retry buttons, history download, and saved pattern/trend analyses.
- **Configuration:** training goal, context, weighted rubric, and coaching instructions, editable by both learner and coach.
- SQLite for local use, PostgreSQL for durable deployment. Schema initialization is automatic and can also run from the command line.

## Local setup

Use Python **3.12**. Open a terminal in this folder:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell and copy the file with `Copy-Item .env.example .env`.

Edit `.env` and set:

```dotenv
OPENAI_API_KEY=your-openai-api-key
BOOTSTRAP_ADMIN_USERNAME=coach_admin
BOOTSTRAP_ADMIN_PASSWORD=your-unique-password-at-least-12-characters
BOOTSTRAP_ADMIN_NAME=Your Name
DATABASE_URL=sqlite:///data/coaching.db
```

Then run:

```bash
python -m coaching.cli init
streamlit run app.py
```

Open the local address printed by Streamlit and log in with the bootstrap credentials. The `init` step is optional: launching the app performs the same initialization. No default password or demo account is shipped. The local database is stored in `data/coaching.db` when you run from this folder.

Bootstrap credentials create the **first account only**, when the users table is empty. They never overwrite an existing account or reset a password. After the first successful setup, remove `BOOTSTRAP_ADMIN_PASSWORD` from your environment/secrets. Restart the app after changing secrets. For local account recovery:

```bash
python -m coaching.cli reset-password coach_admin
```

The command asks for the new password without showing it. It requires trusted access to the app's server and database. It reads environment variables / `.env`, not Streamlit's secrets file; set `DATABASE_URL` when using it against a deployed database.

Without an OpenAI key, login, user management, configuration, the question bank, and saved history still work. Submitting an answer saves it and shows an evaluation error that can be retried after the key is configured. An OpenAI API account with access and quota is required for real AI calls.

## First coaching session

1. Log in as coach and choose **Create a learner account**. Give the learner their username and initial password privately.
2. Open the learner's workspace and set their **Configuration**. Rubric weights must add up to 100%.
3. In **Practice**, answer the displayed question. **Next question** cycles through that learner's own bank; **Choose with AI** selects from it using the profile and recent feedback. Add domain-specific questions for that learner from the coach dashboard when needed.
4. For voice: select **Record**, allow microphone access, record and stop, then click **Transcribe recording**. Review/correct the editable transcript. Click **Submit answer** separately.
5. Read the evaluation or open **Progress**. After two completed evaluations, click **Generate and save analysis**.

The first question is selected locally from the bank, avoiding the latest ten attempted questions where possible. Browsing does not trigger paid API calls. AI calls happen only when choosing with AI, generating a question draft, transcribing, submitting/retrying evaluation, or generating an analysis.

## Deploy to Streamlit Community Cloud with PostgreSQL

1. Create a hosted PostgreSQL database. Get a database connection URL and use the provider's required TLS settings. The database user needs permission to create tables and indexes during first initialization, then read/write access to the app tables.
2. Put this folder's contents in a Git repository. Commit code, `requirements.txt`, and `.streamlit/config.toml`. Do **not** commit `.env`, `.streamlit/secrets.toml`, local databases, or actual credentials; `.gitignore` excludes them.
3. In Streamlit Community Cloud, create an app from that repository, set the entry point to `app.py`, and select Python 3.12. If the code lives in a subfolder, point the entry point there and keep its requirements alongside it.
4. Paste the contents of `.streamlit/secrets.toml.example` into the app's Secrets settings and replace every placeholder. Example:

```toml
OPENAI_API_KEY = "your-real-api-key"
OPENAI_MODEL = "gpt-4.1-mini"
OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
BOOTSTRAP_ADMIN_USERNAME = "coach_admin"
BOOTSTRAP_ADMIN_PASSWORD = "your-unique-password-at-least-12-characters"
BOOTSTRAP_ADMIN_NAME = "Coach"
SESSION_HOURS = 8
```

5. Deploy. First startup creates the tables, seeds ten questions, and creates the first admin. Log in and create learners; then remove the bootstrap password from saved secrets.

Use the provider's documented certificate validation options where available (for example `sslmode=verify-full` with its CA setup). URL-encode special characters in the username/password portion of the database URL. `postgres://` and `postgresql://` URLs are normalized to use the psycopg driver. Provider pooler URLs may be used if they support ordinary PostgreSQL transactions and DDL initialization; follow that provider's instructions.

**Use PostgreSQL for deployment.** The SQLite fallback is intended for a local machine or a server with an explicitly persistent disk. A cloud app's local filesystem is not the durable source of your coaching records. Enable database backups with your database provider. Microphone access needs HTTPS on a remote deployment (localhost also works).

Environment variables take precedence over `.env` values loaded without override; the resulting environment takes precedence over top-level Streamlit secrets. With neither configured, the app uses SQLite at `data/coaching.db` beside the code. Secrets remain server-side and are not fields in user Configuration.

## Architecture and schema

```text
app.py                       Streamlit login, coach dashboard, three workspace pages
coaching/settings.py         Environment / secrets loading
coaching/security.py         Password hashing and principal definitions
coaching/db.py               SQLAlchemy Core schema and engine setup
coaching/repository.py       Persistence with role / ownership checks
coaching/domain.py           Rubric validation and structured response models
coaching/ai.py               OpenAI transcription and structured Responses calls
coaching/service.py          Save/retry evaluation workflow
coaching/cli.py              Initialization and operator password recovery
schema.sql                   PostgreSQL DDL reference generated from the schema
tests/                       Database, authentication, API contract, and app tests
```

Tables:

| Table | Purpose |
|---|---|
| `users` | Unique normalized username, password hash, name, role, active flag, login lockout, session version |
| `user_config` | Goal, context, rubric JSON, instructions, update timestamp/actor |
| `questions` | Each learner's own question bank (scoped by `user_id`), with category and active flag |
| `practice_attempts` | Question snapshot, final answer, optional raw transcript, configuration snapshot, structured evaluation, status, model, timestamps, submitting actor |
| `progress_analyses` | Saved structured analysis, exact source attempt IDs/date range, profile snapshot, model, creating actor |

All foreign keys are enforced in SQLite as well as PostgreSQL. Timestamps are stored/displayed as UTC. `coaching/db.py` is the authoritative executable schema. `schema.sql` is a review/reference artifact; use automatic initialization or `coaching.cli init` to initialize so application defaults and seeds are applied. Initialization creates missing tables; it does **not** migrate existing columns. Back up the database and add an explicit migration before changing a deployed schema.

## Behavior and implementation choices

- Each data operation checks the actor's active state, role, and session version against the database. Learners can only access their own rows. The coach can read/edit any workspace; a banner makes this explicit, and attempts record `created_by` so coach submissions are attributable.
- Disabling or re-enabling a user and resetting their password revokes existing sessions. Disabled learners cannot log in; the coach can still open their saved workspace. Admin accounts cannot be disabled in the UI.
- Login lasts for the Streamlit session, up to `SESSION_HOURS` (1–24, default 8). Reloading may require login again. Session state is cleared on logout and workspace switches. Revocation is checked on the next app interaction, including writes after API calls; it cannot erase content already displayed in a browser.
- Argon2id provides per-password salts. Usernames are case-insensitive and passwords are 12–128 characters. There is no public self-registration, email reset, or persistent-login cookie. Users ask the coach for password resets.
- A database-backed lockout handles repeated failures for known accounts; a short session cooldown also covers unknown names. The app is an MVP for a coach-managed audience, not a full identity service with MFA or distributed IP-based abuse controls.
- The answer and frozen configuration are committed **before** evaluation. Failures remain in Progress, and retry uses the same saved answer/rubric/model. A unique submission ID prevents duplicate attempts on reruns. An atomic, three-minute evaluation lease prevents simultaneous retries; expired leases can be retried. This is on-demand retry, not a background queue. A crash after a provider response but before saving may require another paid API call.
- A question holds its configuration snapshot. Configuration changes apply to the next question. Drafts survive page navigation in the current session; a browser/session restart may discard an **unsubmitted** draft.
- The official answer is the final edited text; the raw transcript is separately retained when voice was used. Audio bytes are held transiently by Streamlit and sent to OpenAI only when transcribing; this app never persists audio to its database or a file store. Logs do not intentionally include passwords, keys, audio, answers, or raw API errors.
- The model returns Pydantic-validated structured output. The app checks that each rubric criterion appears exactly once, with finite scores from 0 to 10, then calculates the weighted overall score itself. Refusals, incomplete output, malformed scores, and API errors are shown as retryable failures.
- Trend analysis uses up to the latest **30 completed evaluations**, oldest to newest, with timestamps, rubric/goal snapshots, and model identifiers. It saves the exact source IDs and asks the model to qualify small samples and changed rubrics. It sends historical evaluations rather than full answer transcripts to keep requests bounded. A score chart/average is descriptive, not proof of improvement.
- The answer limit is 12,000 characters. Recordings must be under 24 MB. The text API has a 60-second request timeout, no automatic retries, and a 5,000 output-token cap. Model names are configurable; use a text model supporting Responses structured outputs.
- OpenAI receives the information needed for the requested operation. Responses requests use `store=False`; that is not a claim of zero retention across OpenAI's services. Learners see a notice before recording/submission. Follow the API account's data settings and your own handling policy.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary databases and mocked OpenAI responses; they do not send coaching data to OpenAI or spend API credits. They exercise secure login, account isolation, disabled sessions, weighted evaluation validation, durable failed attempts, retry/idempotency, rubric snapshots, saved analyses, and Streamlit page flows. The SDK contract tests use a mocked HTTP transport with the real OpenAI client.

Optional PostgreSQL integration tests run the repository contract against a disposable schema when `TEST_POSTGRES_URL` is set. The supplied database account must be allowed to create/drop a schema. Use a **test** database; the test creates and removes only a randomly named `coaching_test_*` schema.

For live verification after configuration, record one short answer, transcribe, edit it, and submit. Confirm that Progress contains the edited answer, original transcript, evaluation, and rubric. Submit a second answer and generate an analysis. Log in as a second learner to confirm their empty workspace, then disable that account from the coach dashboard and check that the next interaction requires login.

## Troubleshooting

- **No account available:** set a bootstrap password and restart the app. If the database already has users, use an existing admin or the recovery command; bootstrap will not recreate accounts.
- **Cannot log in:** usernames are normalized to lowercase. Check account status, wait 15 minutes after five failed attempts, or ask the coach to reset the password.
- **Microphone unavailable:** use HTTPS/localhost and allow microphone access. Typed answers work without a microphone.
- **OpenAI error:** check the key, API billing/quota, and selected model access. The saved answer can be retried from Progress. API access is separate from a ChatGPT subscription.
- **Database error:** verify the URL, encoded password, provider network access, TLS options, and database permissions. Do not paste connection strings containing credentials into public logs.
- **Interrupted evaluation:** wait three minutes, then use Retry evaluation. Refresh the page to reload saved status.
- **Configuration changed but score looks old:** existing questions/attempts keep their snapshots. Start the next question to use the changed settings.

## API and deployment references

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [GPT-4o mini transcribe](https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe)
- [Streamlit audio input](https://docs.streamlit.io/develop/api-reference/widgets/st.audio_input)
- [Streamlit Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
