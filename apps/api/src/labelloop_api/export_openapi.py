"""Export the production application's schema without database or network access."""

import argparse
import json
from pathlib import Path

from labelloop_api.main import app


def render_openapi() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(render_openapi(), encoding="utf-8")


if __name__ == "__main__":
    main()
