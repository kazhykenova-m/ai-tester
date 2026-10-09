"""Run a configured JSON scenario without AI."""

import argparse
import json
from pathlib import Path
from ai_tester.structured import load_project
from run_job import main as run_job
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", default="artifacts")
    parser.add_argument("--mobile", action="store_true")
    parser.add_argument(
        "--headless", action="store_true", help="совместимость: запуск всегда headless"
    )
    args = parser.parse_args()
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    (root / "job.json").write_text(
        json.dumps(
            {
                "url": args.url,
                "scenario": load_project(args.scenario),
                "devices": ["mobile" if args.mobile else "desktop"],
            }
        )
    )
    sys.argv = [sys.argv[0], str(root)]
    run_job()
    results = json.loads((root / "result.json").read_text())
    print(results[0]["reason"])
    print("Отчёт: " + str(root / results[0]["device"] / "report.html"))
    return 0 if results[0]["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
