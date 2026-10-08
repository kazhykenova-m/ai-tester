import argparse
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from ai_tester.agent import run_scenario
from ai_tester.llm import GeminiClient
from ai_tester.scenario import load_scenario


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--vision", action="store_true", help="отправлять скриншоты модели")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--out", default="artifacts")
    args = parser.parse_args()

    load_dotenv(".env")
    scenario = load_scenario(args.scenario)
    llm = GeminiClient()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=300)
        context = browser.new_context(viewport={"width": 1366, "height": 850})
        summary = run_scenario(
            context, scenario, llm, args.url,
            max_steps=args.max_steps, out_dir=args.out, vision=args.vision,
        )
        browser.close()
    verdict = "PASSED" if summary["success"] else "FAILED"
    print(f"{verdict}: {summary['reason']}")
    print(f"Отчёт: {args.out}/report.html")
    sys.exit(0 if summary["success"] else 1)


main()
