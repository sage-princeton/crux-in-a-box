import argparse
import json

from .spec import Run


def main():
    parser = argparse.ArgumentParser(description="Validate and plan a declarative CRUX Run")
    parser.add_argument("command", choices=["validate", "plan"])
    parser.add_argument("specification")
    args = parser.parse_args()
    try:
        run = Run.load(args.specification)
        print(json.dumps(run.plan() if args.command == "plan" else {"valid": True, "run": run.name}, indent=2))
    except (ValueError, TypeError, OSError) as error:
        parser.exit(2, f"crux: {error}\n")


if __name__ == "__main__":
    main()
