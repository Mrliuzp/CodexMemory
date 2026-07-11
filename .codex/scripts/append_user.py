from __future__ import annotations

import json
import sys

from hook_common import handle_user_prompt


if __name__ == "__main__":
    print(handle_user_prompt(json.load(sys.stdin)))
