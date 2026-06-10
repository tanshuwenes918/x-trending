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
    parser.add_argument(
        "--test-common",
        action="store_true",
        help="Test common model names plus models returned by /v1/models.",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model ids to test instead of only LLM_MODEL.",
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


def post_chat(api_key: str, base_url: str, model: str, timeout: int) -> str:
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
    from config.settings import LLM_USER_AGENT

    req = request.Request(
        endpoint(base_url, "/chat/completions"),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": LLM_USER_AGENT,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content[:200]


def list_models(api_key: str, base_url: str, timeout: int) -> list[str]:
    from config.settings import LLM_USER_AGENT

    req = request.Request(
        endpoint(base_url, "/models"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": LLM_USER_AGENT,
        },
        method="GET",
    )
    with request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    models = [item.get("id") for item in result.get("data", []) if item.get("id")]
    return models


def common_models() -> list[str]:
    return [
        "gpt-5.5",
        "gpt-5",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4",
        "o3-mini",
        "o4-mini",
        "deepseek-chat",
        "deepseek-reasoner",
        "claude-3-5-sonnet",
        "claude-3-7-sonnet",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ]


def unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def main() -> int:
    load_dotenv()
    args = parse_args()

    from config.settings import (
        LLM_API_KEY,
        LLM_BASE_URL,
        LLM_MODEL,
        LLM_REQUEST_TIMEOUT,
        LLM_USER_AGENT,
    )

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
    print("User-Agent:", LLM_USER_AGENT)

    try:
        discovered_models = []
        if args.list_models or args.test_common:
            discovered_models = list_models(LLM_API_KEY, LLM_BASE_URL, LLM_REQUEST_TIMEOUT)
            print("Models:")
            for model in discovered_models[:100]:
                print(f"- {model}")
            if not discovered_models:
                print("(No models returned.)")

        if args.models:
            models_to_test = unique(args.models.split(","))
        elif args.test_common:
            models_to_test = unique(discovered_models + common_models())
        else:
            models_to_test = [LLM_MODEL]

        print("\nChat tests:")
        usable_models = []
        for model in models_to_test:
            try:
                content = post_chat(LLM_API_KEY, LLM_BASE_URL, model, LLM_REQUEST_TIMEOUT)
                usable_models.append(model)
                print(f"OK   {model}   {content}")
            except HTTPError as exc:
                print(f"FAIL {model}   {http_error_detail(exc).replace(chr(10), ' ')[:300]}")
            except URLError as exc:
                print(f"FAIL {model}   {exc}")

        print("\nUsable models:")
        if usable_models:
            for model in usable_models:
                print(f"- {model}")
        else:
            print("(none)")
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
