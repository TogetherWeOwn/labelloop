"""Compile the two licence-clean canonical fixture releases for offline verification."""

import argparse
from pathlib import Path

from labelloop_compiler.compiler import compile_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new build directory")
    parser.add_argument("--fixture-root", type=Path, default=Path("fixtures"))
    args = parser.parse_args()
    import sys

    sys.path.insert(0, str(args.fixture_root.resolve()))
    from load_canonical import FixtureDomain

    for domain in ("commons-office-keyboards", "cc0-interface"):
        fixture = FixtureDomain(domain)
        for recipe in ("full-letterbox-640-v1", "full-letterbox-320-v1"):
            compile_dataset(fixture.release, recipe, args.output / domain / recipe, fixture.images)


if __name__ == "__main__":
    main()
