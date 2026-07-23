from .entrypoints.cli import *


def main():
    from .entrypoints.cli import main as implementation_main

    return implementation_main()


if __name__ == "__main__":
    main()
