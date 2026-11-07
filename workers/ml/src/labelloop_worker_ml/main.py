"""ML vendor imports stay inside this package."""


def runtime_version() -> str:
    from ultralytics import __version__

    return __version__


def main() -> None:
    print(f"ML worker: ultralytics {runtime_version()}")


if __name__ == "__main__":
    main()
