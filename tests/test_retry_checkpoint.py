"""Preserve useful scraped reports when the LLM service is unavailable."""

import argparse
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

import config.settings as settings
import exporters.feishu_exporter as exporter_module
import main as entrypoint
import processors.data_processor as processor_module
import processors.llm_processor as llm_module
import scrapers.trending_scraper as scraper_module


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    args = argparse.Namespace(output_format="both", dry_run=False, input_json="")
    monkeypatch.setattr(entrypoint, "parse_args", lambda: args)
    monkeypatch.setattr(entrypoint, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings, "DRY_RUN", False)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)

    observed = MagicMock()
    observed.scraper.scrape_all.return_value = {"Global": {"Technology": [{"tweets": []}]}}
    observed.args = args
    observed.output_dir = tmp_path
    observed.report = {
        "timestamp": "2026-10-24T14:00:00+08:00",
        "groups": [
            {
                "name": "AI / Tech / Creator Tools",
                "items": [
                    {
                        "tweet_url": "https://x.com/u/status/1",
                        "content": "A new AI music tool",
                        "summary": "待生成摘要",
                    }
                ],
            }
        ],
        "top_actions": [],
    }
    observed.processor.process.side_effect = lambda raw: deepcopy(observed.report)
    observed.llm.enrich.side_effect = lambda report: {**report, "llm_used": True}
    observed.exporter.export.return_value = True
    monkeypatch.setattr(scraper_module, "TrendingScraper", lambda: observed.scraper)
    monkeypatch.setattr(processor_module, "DataProcessor", lambda: observed.processor)
    monkeypatch.setattr(llm_module, "LLMProcessor", lambda: observed.llm)
    monkeypatch.setattr(exporter_module, "FeishuExporter", lambda: observed.exporter)
    return observed


def test_llm_failure_preserves_original_nonempty_input_and_can_replay(pipeline, caplog):
    original_report = deepcopy(pipeline.report)
    failure = RuntimeError("LLM server overloaded")

    def fail_after_mutation(report):
        report["groups"][0]["items"].clear()
        raise failure

    pipeline.llm.enrich.side_effect = fail_after_mutation

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert exc.value.__context__ is failure
    checkpoints = list(pipeline.output_dir.glob("retry_input_*.json"))
    assert len(checkpoints) == 1
    assert entrypoint.read_json_input(checkpoints[0]) == original_report
    assert not list(pipeline.output_dir.glob("x_trending_*.json"))
    pipeline.exporter.export.assert_not_called()
    assert "--input-json" in caplog.text
    assert str(checkpoints[0]) in caplog.text

    pipeline.args.input_json = str(checkpoints[0])
    pipeline.llm.enrich.side_effect = lambda report: {**report, "llm_used": True}
    pipeline.scraper.scrape_all.reset_mock()
    pipeline.processor.process.reset_mock()

    entrypoint.main()

    pipeline.scraper.scrape_all.assert_not_called()
    pipeline.processor.process.assert_not_called()
    pipeline.exporter.export.assert_called_once_with({**original_report, "llm_used": True})
    completed = list(pipeline.output_dir.glob("x_trending_*.json"))
    assert len(completed) == 1
    assert entrypoint.read_json_input(completed[0])["llm_used"] is True
    assert list(pipeline.output_dir.glob("retry_input_*.json")) == checkpoints


@pytest.mark.parametrize("use_input_json", [False, True])
def test_empty_report_is_not_checkpointed_or_exported(pipeline, use_input_json):
    pipeline.report = {"groups": [{"items": []}], "top_actions": []}
    if use_input_json:
        input_path = pipeline.output_dir / "empty.json"
        input_path.write_text('{"groups": [{"items": []}]}', encoding="utf-8")
        pipeline.args.input_json = str(input_path)

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert not list(pipeline.output_dir.glob("retry_input_*.json"))
    assert not list(pipeline.output_dir.glob("x_trending_*.json"))
    pipeline.llm.enrich.assert_not_called()
    pipeline.exporter.export.assert_not_called()


def test_successful_llm_run_has_no_retry_checkpoint(pipeline):
    entrypoint.main()

    assert not list(pipeline.output_dir.glob("retry_input_*.json"))
    assert len(list(pipeline.output_dir.glob("x_trending_*.json"))) == 1
    pipeline.exporter.export.assert_called_once()


def test_checkpoint_write_error_does_not_replace_original_llm_failure(pipeline, monkeypatch, caplog):
    failure = RuntimeError("LLM server overloaded")
    pipeline.llm.enrich.side_effect = failure
    checkpoint_writer = MagicMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(entrypoint, "write_retry_checkpoint", checkpoint_writer)

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert exc.value.__context__ is failure
    checkpoint_writer.assert_called_once_with(pipeline.report)
    assert "Failed to save LLM retry input" in caplog.text
    assert "LLM server overloaded" in caplog.text
    assert not list(pipeline.output_dir.glob("x_trending_*.json"))
    pipeline.exporter.export.assert_not_called()
