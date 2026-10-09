import base64
import os
import time

import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_CODES = {429, 500, 502, 503, 504}
DEFAULT_FALLBACKS = "gemini-2.5-flash"


class GeminiClient:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (see .env)")
        main = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
        fallbacks = os.environ.get("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACKS)
        self.models = [main] + [m.strip() for m in fallbacks.split(",") if m.strip()]
        # пауза между запросами, чтобы не упираться в лимиты бесплатного тарифа
        self.min_interval = float(os.environ.get("GEMINI_MIN_INTERVAL", "4"))
        self._last_call = 0.0

    def _pace(self):
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _request(self, model, prompt, image=None):
        parts = [{"text": prompt}]
        if image:
            data = base64.b64encode(image).decode("ascii")
            parts.append({"inline_data": {"mime_type": "image/png", "data": data}})
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        self._pace()
        return requests.post(
            API_URL.format(model=model),
            headers={"x-goog-api-key": self.api_key},
            json=body,
            timeout=(10, 120),
        )

    def generate(self, prompt, image=None, retries=2):
        last_error = "no attempts"
        for model in self.models:
            for attempt in range(retries):
                try:
                    resp = self._request(model, prompt, image)
                except requests.exceptions.RequestException as exc:
                    last_error = f"{model}: {type(exc).__name__}"
                    time.sleep(3 * (attempt + 1))
                    continue
                if resp.ok:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    return "".join(p.get("text", "") for p in parts)
                last_error = f"{model}: {resp.status_code} {resp.text[:200]}"
                if resp.status_code not in RETRY_CODES:
                    break
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Gemini API unavailable. Last error: {last_error}")
