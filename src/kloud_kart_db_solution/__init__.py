import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compose(env_file: str, compose_file: str, *args: str) -> int:
    command = [
        "docker",
        "compose",
        "--env-file",
        env_file,
        "-f",
        compose_file,
        *args,
    ]
    try:
        return subprocess.call(command, cwd=PROJECT_ROOT)
    except FileNotFoundError:
        print("Docker was not found on PATH.", file=sys.stderr)
        return 127


def test_db(*args: str) -> int:
    return _compose(".env", "compose.yaml", *(args or sys.argv[1:] or ["up", "-d"]))


def prod_db(*args: str) -> int:
    return _compose(".env.prod", "compose.prod.yaml", *(args or sys.argv[1:] or ["up", "-d"]))


def test_db_exec() -> int:
    return test_db("exec", "postgres", *(sys.argv[1:] or ["sh"]))


def prod_db_exec() -> int:
    return prod_db("exec", "postgres", *(sys.argv[1:] or ["sh"]))


def _run_python(*args: str) -> int:
    # Default to the test configuration. Keep explicitly exported settings
    # and variables uv supplied through --env-file.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return subprocess.call([sys.executable, *args, *sys.argv[1:]], cwd=PROJECT_ROOT)


def race_test() -> int:
    return _run_python("provided_resources/race_test.py")


def flash_sale_test() -> int:
    return _run_python("scripts/flash_sale_test.py")


def reconcile_app() -> int:
    return _run_python(
        "-m", "streamlit", "run", "scripts/reconcile_app.py", "--server.address", "127.0.0.1"
    )
