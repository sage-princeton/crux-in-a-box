import argparse
import json
from pathlib import Path

from .spec import Run


def main():
    parser = argparse.ArgumentParser(description="Validate and plan a declarative CRUX Run")
    parser.add_argument("command", choices=["validate", "plan", "preflight", "create", "destroy"])
    parser.add_argument("specification")
    parser.add_argument("--state", type=Path, help="private deployment receipt outside the checkout")
    args = parser.parse_args()
    try:
        run = Run.load(args.specification)
        if args.command in ("preflight", "create", "destroy"):
            if not args.state:
                raise ValueError("--state is required for resource mutations")
            from .aws import AWS
            from .lifecycle import Lifecycle
            lifecycle = Lifecycle(run, args.state, AWS().registry())
            if args.command == "preflight":
                lifecycle.check()
                print(json.dumps({"valid": True, "run": run.name}))
                return
            result = lifecycle.apply() if args.command == "create" else lifecycle.destroy()
            print(json.dumps({"run": run.name, "phase": result["phase"], "state": str(args.state)}, indent=2))
        else:
            print(json.dumps(run.plan() if args.command == "plan" else {"valid": True, "run": run.name}, indent=2))
    except (ValueError, TypeError, OSError) as error:
        parser.exit(2, f"crux: {error}\n")


if __name__ == "__main__":
    main()
