import sys

from modules.cli import main


if __name__ == "__main__":
    if len(sys.argv) == 1:
        main(["tui"])
    else:
        main()
