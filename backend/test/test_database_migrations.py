import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.database as database
from app.models.assignment import Assignment
from app.services.auth import create_user


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.previous_engine = database.engine
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.engine = self.engine

    def tearDown(self):
        self.engine.dispose()
        database.engine = self.previous_engine

    def test_fresh_database_migrates_and_is_idempotent(self):
        database.init_db()
        database.init_db()

        inspector = inspect(self.engine)
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("submissions")}
            - {"id", "assignment_id", "source_code", "score", "status"},
            {"filename"},
        )
        self.assertTrue(
            {
                "auto_approve",
                "proposal",
                "grading_rubric",
            }.issubset(
                {column["name"] for column in inspector.get_columns("grading_sessions")}
            )
        )
        self.assertIn(
            "queued_at",
            {
                column["name"]
                for column in inspect(self.engine).get_columns(
                    "submission_upload_batches"
                )
            },
        )
        with self.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        self.assertEqual(version, "20261026_0005")

    def test_existing_unversioned_database_is_adopted_without_losing_rows(self):
        config = Config(str(database.BACKEND_DIR / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "20261024_0001")
            connection.execute(
                text(
                    "INSERT INTO assignments "
                    "(id, title, description, language, rubric) "
                    "VALUES (1, 'Legacy assignment', NULL, 'python', '{}')"
                )
            )
            connection.execute(text("DELETE FROM alembic_version"))

        database.init_db()

        with self.engine.connect() as connection:
            title = connection.execute(
                text("SELECT title FROM assignments WHERE id = 1")
            ).scalar_one()
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        self.assertEqual(title, "Legacy assignment")
        self.assertEqual(version, "20261026_0005")
        self.assertIn(
            "grading_rubric",
            {
                column["name"]
                for column in inspect(self.engine).get_columns("grading_sessions")
            },
        )

    def test_first_user_claims_assignments_without_an_owner(self):
        database.init_db()
        with Session(self.engine) as session:
            session.add(
                Assignment(
                    title="Legacy assignment",
                    language="python",
                    min_score=0,
                    max_score=10,
                )
            )
            session.commit()

            user = create_user(session, "first@example.com")
            assignment = session.query(Assignment).one()

            self.assertEqual(assignment.owner_id, user.id)


if __name__ == "__main__":
    unittest.main()
