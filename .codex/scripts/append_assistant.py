from __future__ import annotations

import json
import sys

from hook_common import handle_stop


if __name__ == "__main__":
    handle_stop(json.load(sys.stdin))
