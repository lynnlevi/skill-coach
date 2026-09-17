-- PostgreSQL DDL reference. Generated from coaching/db.py.
-- Use python -m coaching.cli init for initialization and seeds.
-- Defaults are supplied by the application, not SQL server defaults.


CREATE TABLE questions (
	id SERIAL NOT NULL, 
	text TEXT NOT NULL, 
	category VARCHAR(100) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (text)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash TEXT NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	active BOOLEAN NOT NULL, 
	session_version INTEGER NOT NULL, 
	failed_logins INTEGER NOT NULL, 
	locked_until FLOAT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT valid_role CHECK (role IN ('admin', 'user')), 
	UNIQUE (username)
);

CREATE TABLE practice_attempts (
	id SERIAL NOT NULL, 
	submission_id VARCHAR(36) NOT NULL, 
	user_id INTEGER NOT NULL, 
	created_by INTEGER NOT NULL, 
	question_id INTEGER NOT NULL, 
	question_text TEXT NOT NULL, 
	answer TEXT NOT NULL, 
	answer_source VARCHAR(10) NOT NULL, 
	raw_transcript TEXT, 
	transcription_model VARCHAR(100), 
	config_snapshot JSON NOT NULL, 
	evaluation JSON, 
	overall_score FLOAT, 
	status VARCHAR(16) NOT NULL, 
	error_message TEXT, 
	evaluation_lease_until FLOAT NOT NULL, 
	evaluation_token VARCHAR(36), 
	model VARCHAR(100) NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	evaluated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT valid_attempt_status CHECK (status IN ('pending', 'evaluating', 'failed', 'completed')), 
	CONSTRAINT valid_answer_source CHECK (answer_source IN ('typed', 'audio')), 
	CONSTRAINT valid_score CHECK (overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 10)), 
	UNIQUE (submission_id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(question_id) REFERENCES questions (id)
);

CREATE INDEX ix_attempts_user_time ON practice_attempts (user_id, created_at);

CREATE TABLE progress_analyses (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	created_by INTEGER NOT NULL, 
	attempt_ids JSON NOT NULL, 
	attempt_start TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempt_end TIMESTAMP WITH TIME ZONE NOT NULL, 
	config_snapshot JSON NOT NULL, 
	analysis JSON NOT NULL, 
	model VARCHAR(100) NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
);

CREATE INDEX ix_analyses_user_time ON progress_analyses (user_id, created_at);

CREATE TABLE user_config (
	user_id INTEGER NOT NULL, 
	training_goal TEXT NOT NULL, 
	context TEXT NOT NULL, 
	rubric JSON NOT NULL, 
	coaching_instructions TEXT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_by INTEGER NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
);
