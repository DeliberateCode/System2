"""Run the installed channel's bundled compiler for the current project."""

from pathlib import Path
import sys


def main():
    if not sys.flags.isolated or not sys.flags.no_site:
        raise SystemExit("Run the bundled compiler with python3 -ISB.")
    if sys.version_info < (3, 11):
        raise SystemExit("System2 overlay commands require Python 3.11 or newer.")
    verbs = {"compile", "from-lock", "uninstall", "doctor", "profile"}
    if len(sys.argv) < 2 or sys.argv[1] not in verbs:
        raise SystemExit("Usage: python3 -ISB run.py {compile|from-lock|uninstall|doctor|profile} [options]")

    root = Path(__file__).resolve().parent
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from system2_compiler.cli import main as compile_command

    verb, *args = sys.argv[1:]
    options = ["--target", "codex", "--workspace", "--project", str(Path.cwd())]
    if verb != "profile":
        options += ["--base", str(root / "base")]
    return compile_command([verb, *args, *options])


if __name__ == "__main__":
    raise SystemExit(main())
