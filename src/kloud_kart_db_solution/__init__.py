import subprocess
import sys
from pathlib import Path


def _compose(env_file: str, compose_file: str, *args: str) -> int:
    project_root = Path(__file__).resolve().parents[2]
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
        return subprocess.call(command, cwd=project_root)
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
