import logging
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


logger = logging.getLogger(__name__)
processes: list[subprocess.Popen] = []
stop_requested = False


def stop_children() -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()


def handle_shutdown(_signum, _frame) -> None:
    global stop_requested
    stop_requested = True
    stop_children()


def wait_for_api(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The API stopped before becoming ready.")
        try:
            with urlopen("http://127.0.0.1:8000/", timeout=3):
                return
        except (OSError, URLError):
            time.sleep(1)
    raise TimeoutError("The API did not become ready within 180 seconds.")


def main() -> int:
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
    )
    processes.append(api)

    try:
        wait_for_api(api)
        if stop_requested:
            return 0

        worker = subprocess.Popen(
            [sys.executable, "-m", "app.workers.submission_upload"]
        )
        processes.append(worker)

        while not stop_requested:
            for process in processes:
                exit_code = process.poll()
                if exit_code is not None:
                    logger.error(
                        "Service process %s exited with code %s",
                        process.args,
                        exit_code,
                    )
                    return exit_code or 1
            time.sleep(1)
        return 0
    except (TimeoutError, RuntimeError):
        logger.exception("Could not start Render services.")
        return 1
    finally:
        stop_children()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
