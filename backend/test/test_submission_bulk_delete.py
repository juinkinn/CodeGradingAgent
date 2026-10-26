import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.submission import delete_many, get_many
from app.database import Base
from app.models.assignment import Assignment
from app.models.grading_session import GradingSession, SessionSubmission
from app.models.submission import Submission
from app.models.submission_upload import SubmissionUploadBatch, SubmissionUploadItem


class SubmissionBulkDeleteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_bulk_delete_removes_session_links_and_preserves_upload_history(self):
        assignment = Assignment(
            title="Test assignment",
            language="python",
            min_score=0,
            max_score=10,
            rubric={},
        )
        submission = Submission(
            assignment=assignment,
            filename="main.py",
            source_code="print('hello')",
        )
        session = GradingSession(assignment=assignment, type="AI_ASSISTANT")
        session.session_submissions.append(SessionSubmission(submission=submission))
        batch = SubmissionUploadBatch(assignment=assignment, status="COMPLETED")
        item = SubmissionUploadItem(
            batch=batch,
            submission=submission,
            filename="main.py",
            status="COMPLETED",
        )
        self.db.add_all([assignment, session, batch, item])
        self.db.commit()
        submission_id = submission.id

        deleted_ids = delete_many(self.db, get_many(self.db, [submission_id]))

        self.assertEqual(deleted_ids, [submission_id])
        self.assertIsNone(self.db.get(Submission, submission_id))
        self.assertEqual(
            self.db.query(SessionSubmission).filter_by(session_id=session.id).count(),
            0,
        )
        self.assertIsNone(
            self.db.query(SubmissionUploadItem)
            .filter_by(id=item.id)
            .one()
            .submission_id
        )


if __name__ == "__main__":
    unittest.main()
