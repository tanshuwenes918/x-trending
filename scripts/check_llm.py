#!/usr/bin/env python3
"""Check OpenAI-compatible LLM configuration without scraping X."""

import argparse
import json
import sys
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))


def parse_args():
    parser = argparse.ArgumentParser(description="Check LLM connectivity.")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Call /v1/models and print available model ids if the endpoint allows it.",
    )
    return parser.parse_args()


def endpoint(base_url: str, path: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/v1"):
        return f"{clean}{path}"
    return f"{clean}/v1{path}"


def http_error_detail(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    detail = f"HTTP {exc.code} {exc.reason}"
    if body:
        detail = f"{detail}\n{body[:1000]}"
    return detail


def post_chat(api_key: str, base_url: str, model: str, timeout: int) -> None:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "只返回JSON，不要返回Markdown。"},
            {"role": "user", "content": '{"status":"ok"}'},
        ],
        "temperature": 0,
        "max_tokens": 50,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        endpoint(base_url, "/chat/completions"),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    print("Chat test: OK")
    print("Response:", content[:200])


def list_models(api_key: str, base_url: str, timeout: int) -> None:
    req = request.Request(
        endpoint(base_url, "/models"),
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    models = [item.get("id") for item in result.get("data", []) if item.get("id")]
    print("Models:")
    for model in models[:100]:
        print(f"- {model}")
    if not models:
        print("(No models returned.)")


def main() -> int:
    load_dotenv()
    args = parse_args()

    from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_REQUEST_TIMEOUT

    missing = [
        name
        for name, value in {
            "LLM_API_KEY": LLM_API_KEY,
            "LLM_BASE_URL": LLM_BASE_URL,
            "LLM_MODEL": LLM_MODEL,
        }.items()
        if not value
    ]
    if missing:
        print("Missing:", ", ".join(missing))
        return 1

    print("LLM base URL:", LLM_BASE_URL.rstrip("/"))
    print("LLM model:", LLM_MODEL)
    print("Timeout:", LLM_REQUEST_TIMEOUT)

    try:
        if args.list_models:
            list_models(LLM_API_KEY, LLM_BASE_URL, LLM_REQUEST_TIMEOUT)
        post_chat(LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_REQUEST_TIMEOUT)
    except HTTPError as exc:
        print("LLM check failed:")
        print(http_error_detail(exc))
        return 1
    except URLError as exc:
        print("LLM check failed:")
        print(exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
