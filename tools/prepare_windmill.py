"""Copy canonical Questy modules into Windmill's relative-import tree."""

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "modules"
TARGET = ROOT / "f" / "questy" / "runtime"
MODULES = (
    "api.py",
    "accounts.py",
    "filters.py",
    "gateway.py",
    "notifications.py",
    "quest.py",
    "rpc.py",
    "runner.py",
)


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "__init__.py").write_text("", encoding="utf-8")
    for name in MODULES:
        shutil.copyfile(SOURCE / name, TARGET / name)

    # The Windmill script imports the reusable execution function without the
    # CLI parser or terminal-specific behavior.
    (TARGET / "cli_service.py").write_text(
        (SOURCE / "cli.py").read_text(encoding="utf-8")
        .split("\ndef cmd_tui", 1)[0]
        .replace("from .accounts import", "from .accounts import", 1),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
