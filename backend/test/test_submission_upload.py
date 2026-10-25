import io
import unittest
import zipfile

from app.services.submission_upload import (
    MAX_SOURCE_FILE_BYTES,
    UploadValidationError,
    collect_source_files,
)


class CollectSourceFilesTests(unittest.TestCase):
    def test_collects_manual_source_file(self):
        files = collect_source_files([("main.PY", b"print('hello')")])

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "main.PY")
        self.assertEqual(files[0].source_code, "print('hello')")

    def test_accepts_all_supported_source_extensions(self):
        uploads = [
            ("main.cpp", b""),
            ("main.c", b""),
            ("Main.java", b""),
            ("main.py", b""),
        ]

        files = collect_source_files(uploads)

        self.assertEqual(
            [file.filename for file in files], [name for name, _ in uploads]
        )

    def test_reads_supported_files_from_zip_without_extracting_paths(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("../../student/main.cpp", "int main() {}")
            archive.writestr("README.txt", "ignored")

        files = collect_source_files([("submissions.zip", archive_buffer.getvalue())])

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "main.cpp")
        self.assertEqual(files[0].source_code, "int main() {}")

    def test_rejects_unsupported_standalone_files(self):
        with self.assertRaises(UploadValidationError):
            collect_source_files([("notes.txt", b"not source")])

    def test_rejects_invalid_utf8(self):
        with self.assertRaises(UploadValidationError):
            collect_source_files([("main.py", b"\xff")])

    def test_rejects_source_file_over_size_limit(self):
        with self.assertRaises(UploadValidationError) as raised:
            collect_source_files([("main.py", b"x" * (MAX_SOURCE_FILE_BYTES + 1))])

        self.assertEqual(raised.exception.status_code, 413)

    def test_rejects_invalid_zip(self):
        with self.assertRaises(UploadValidationError):
            collect_source_files([("submissions.zip", b"not a zip")])

    def test_rejects_zip_without_supported_sources(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("README.txt", "no source")

        with self.assertRaises(UploadValidationError):
            collect_source_files([("submissions.zip", archive_buffer.getvalue())])


if __name__ == "__main__":
    unittest.main()
