"""Isolated deterministic browser process for one Telegram job."""

import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from ai_tester.network import install_guard
from ai_tester.runner import run
from ai_tester.security import validate_url


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    args = parser.parse_args()
    root = Path(args.job)
    job = json.loads((root / "job.json").read_text())
    if job.get("kind") == "source":
        from ai_tester.report import write_reports
        from ai_tester.source_audit import audit_zip

        summary = audit_zip(root / "source.zip")
        write_reports(root / "source", summary)
        (root / "source.zip").unlink()
        results = [summary]
    else:
        validate_url(job["url"])
        allowed = {
            v.strip().lower()
            for v in os.getenv("TEST_WRITE_HOSTS", "").split(",")
            if v.strip()
        }
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for device in job["devices"]:
                (root / "progress.txt").write_text("Проверяются страницы: " + device)
                mobile = device == "mobile"
                context = browser.new_context(
                    viewport={
                        "width": 390 if mobile else 1366,
                        "height": 844 if mobile else 850,
                    },
                    is_mobile=mobile,
                    has_touch=mobile,
                    service_workers="block",
                    accept_downloads=False,
                )
                blocked = []
                ignored = set()
                install_guard(context, allowed, blocked, ignored)
                results.append(
                    run(
                        context,
                        job["scenario"],
                        job["url"],
                        root / device,
                        device,
                        allowed,
                        ignored,
                        blocked,
                        references=job.get("references", []),
                    )
                )
                context.close()
            browser.close()
    (root / "result.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
