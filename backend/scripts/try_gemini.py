"""Synthetic Gemini trial; optional isolated MongoDB category persistence."""

import argparse
import json
import math
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.schemas.extraction import ExtractionInput, SourceRecord
from app.services.gemini import CONTRACT_VERSION, PROMPT_VERSION, GeminiError, GeminiExtractor

SYNTHETIC_CATEGORY_OWNER = "__raphael_synthetic_categories_v1__"


def analyze_with_categories(settings, extractor, data):
    from app.db import DatabaseConnection
    from app.repositories.content_types import ContentTypesRepository
    from app.services.classification import ClassificationService
    connection = DatabaseConnection(settings)
    try:
        connection.open()
        if connection.database is None:
            raise GeminiError("category_storage_unavailable")
        repository = ContentTypesRepository(connection.database['content_types'])
        return ClassificationService(extractor, repository).analyze(SYNTHETIC_CATEGORY_OWNER, data)
    except GeminiError:
        raise
    except Exception:
        # Database errors can contain credentials; never expose raw driver messages.
        raise GeminiError("category_storage_failed") from None
    finally:
        connection.close()


def example(case: str) -> ExtractionInput:
    stamp = datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)
    if case == "context":
        return ExtractionInput(
            target=SourceRecord(record_id="sample_2", content="茶飲70元，甜點110元。", created_at=stamp),
            context=[SourceRecord(record_id="sample_1", content="今天買了茶飲和甜點，共180元。", created_at=stamp)],
            timezone="Asia/Taipei",
        )
    text = "asdfasdfasdf" if case == "noise" else "今天上陶藝課很開心，下個月想去京都，但還沒決定。"
    return ExtractionInput(target=SourceRecord(record_id="sample_1", content=text, created_at=stamp), timezone="Asia/Taipei")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["multi", "context", "noise"], default="multi")
    parser.add_argument("--dry-run", action="store_true", help="Show synthetic input without calling Gemini")
    parser.add_argument("--with-categories", action="store_true", help="Read/write only synthetic-owner content types in MongoDB; never read records")
    parser.add_argument("--read-timeout", type=float, default=120, help="Response inactivity timeout in seconds (1-300, default 120)")
    args = parser.parse_args()
    if not math.isfinite(args.read_timeout) or not 1 <= args.read_timeout <= 300:
        parser.error("--read-timeout must be between 1 and 300 seconds")
    data = example(args.case)
    if args.dry_run:
        print(data.model_dump_json(indent=2))
        print("Dry run: no API request or database access.")
        return 0
    settings = get_settings()
    started = time.monotonic()
    category_id = None
    try:
        extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model, read_timeout=args.read_timeout)
        if args.with_categories:
            result, category_id = analyze_with_categories(settings, extractor, data)
        else:
            result = extractor.analyze(data)
    except GeminiError as exc:
        print(f"Gemini test failed: {exc.reason}. Check backend configuration/network/quota.")
        print(f"Elapsed: {time.monotonic() - started:.1f}s; read timeout: {args.read_timeout:g}s.")
        if exc.status_code is not None:
            print(f"HTTP status: {exc.status_code}")
        hints = {
            "connect_timeout": "Connection/TLS establishment timed out. Check network access to generativelanguage.googleapis.com; increasing read timeout will not fix this.",
            "read_timeout": "Timed out waiting for response data. The request may already have reached Google; avoid repeated immediate retries. No successful JSON response was received.",
            "write_timeout": "Timed out sending the request. Check network stability.",
            "pool_timeout": "Timed out acquiring a connection.",
            "network_error": "Network request failed before a usable response. Check DNS, TLS and direct HTTPS connectivity.",
        }
        if exc.reason in hints:
            print(hints[exc.reason])
        return 1
    print(json.dumps({"provider": "gemini", "model": settings.gemini_model,
                      "prompt_version": PROMPT_VERSION, "contract_version": CONTRACT_VERSION,
                      "content_type_id": category_id,
                      "analysis": result.model_dump()}, ensure_ascii=False, indent=2))
    print("Preview only: no records or memories were written.")
    if args.with_categories:
        print("Synthetic-owner content type was reused or created in MongoDB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
