from __future__ import annotations

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-memory-worker")
    parser.add_argument("--schedule", default="02:00")
    parser.parse_args()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
