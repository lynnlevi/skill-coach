"""Operator-only initialization and recovery from a trusted server terminal."""
import argparse
import getpass

from sqlalchemy import select

from .db import build_engine, users
from .repository import Repository
from .security import normalize_username
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Initialize the coaching database or recover an account.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create missing tables, seed questions, and bootstrap the first admin from env.")
    reset = sub.add_parser("reset-password", help="Reset an existing user's password using a hidden prompt.")
    reset.add_argument("username")
    args = parser.parse_args()
    settings = Settings.load()
    engine = build_engine(settings.database_url)
    repo = Repository(engine)
    try:
        repo.initialize(settings)
        if args.command == "init":
            print("Database initialized. First admin is configured." if repo.has_users() else
                  "Schema and question bank initialized. Set BOOTSTRAP_ADMIN_PASSWORD and run init again to create the first admin.")
        else:
            with engine.begin() as conn:
                user_id = conn.scalar(select(users.c.id).where(users.c.username == normalize_username(args.username)))
                if not user_id:
                    parser.error("No such account.")
                password = getpass.getpass("New password (12–128 characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("Passwords do not match.")
                repo._reset_password(conn, user_id, password)
            print("Password changed; existing sessions revoked. Account active/disabled status is unchanged.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
