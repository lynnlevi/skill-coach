# Validation

Validated locally on 17 September 2026 using Python 3.12.12 and the runtime versions pinned in `requirements.txt`.

## Results

- `python -m pytest -q`: **26 passed**.
- `pip check`: **no broken requirements**.
- PostgreSQL DDL compiled successfully from the SQLAlchemy schema into `schema.sql`.

The passing tests cover:

- Salted Argon2id hashing, password verification, bootstrap idempotency, normalized usernames, login lockout, and password reset session revocation.
- Learner workspace isolation, administrator access, disabling/re-enabling accounts, and preserving saved history.
- Durable answer/transcript storage, frozen configuration snapshots, duplicate submission protection, retrying failed evaluations, and evaluation lease recovery.
- Real OpenAI SDK request/response parsing with a mocked HTTP transport, including structured JSON schema, edited final-answer submission, WAV transcription requests, malformed output, refusals, and safe API errors.
- Rubric weighting and validation, chronological analysis input, and saved source attempt IDs.
- Streamlit application flows for login, account creation, workspace opening, typed practice, page navigation, configuration, transcript review/editing, evaluation retries, score charts, and saved trend analysis.

## Not verified in this environment

- Live OpenAI transcription/evaluation calls: no API credentials were supplied. Tests use mocked provider responses and spend no API credits.
- A live PostgreSQL connection: no PostgreSQL server or connection URL was supplied. The optional repository tests can run against PostgreSQL using `TEST_POSTGRES_URL`; see the README.
- Browser microphone permissions and physical recording: the Streamlit tests substitute the audio widget's returned recording because AppTest does not provide microphone capture.
- Deployment to a hosting account: deployment was not requested or performed.

Follow the README's live verification steps after supplying your own API key and deployment database.
