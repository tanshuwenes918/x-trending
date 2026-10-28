"""Failure alerts receive useful pipeline stages without raw exception content."""

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
    observed = MagicMock()
    observed.scraper.scrape_all.return_value = {"Global": {"Technology": [{"tweets": []}]}}
    observed.args = argparse.Namespace(output_format="both", dry_run=False, input_json="")
    observed.report = {
        "timestamp": "2026-10-28T11:00:00+08:00",
        "groups": [{"name": "AI", "items": [{"summary": "A usable item"}]}],
    }
    observed.output_file = tmp_path / "github-output"
    observed.processor.process.side_effect = lambda raw: deepcopy(observed.report)
    observed.llm.enrich.side_effect = lambda data: data
    observed.exporter.export.return_value = True
    monkeypatch.setattr(entrypoint, "parse_args", lambda: observed.args)
    monkeypatch.setattr(entrypoint, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings, "DRY_RUN", False)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "reports")
    monkeypatch.setenv("GITHUB_OUTPUT", str(observed.output_file))
    monkeypatch.setattr(scraper_module, "TrendingScraper", lambda: observed.scraper)
    monkeypatch.setattr(processor_module, "DataProcessor", lambda: observed.processor)
    monkeypatch.setattr(llm_module, "LLMProcessor", lambda: observed.llm)
    monkeypatch.setattr(exporter_module, "FeishuExporter", lambda: observed.exporter)
    return observed


@pytest.mark.parametrize(
    ("failure_at", "expected_stage"),
    [
        ("init", "初始化"),
        ("load", "读取保存数据"),
        ("scrape", "X 抓取"),
        ("process", "数据整理"),
        ("model", "模型摘要"),
        ("save", "保存结果"),
        ("export", "飞书推送"),
    ],
)
def test_pipeline_reports_the_actual_failed_stage_without_exception_data(
    pipeline, monkeypatch, failure_at, expected_stage
):
    failure = RuntimeError("secret-token=private-value\nhttps://private.invalid/key")
    if failure_at == "init":
        monkeypatch.setattr(scraper_module, "TrendingScraper", MagicMock(side_effect=failure))
    elif failure_at == "load":
        pipeline.args.input_json = "saved.json"
        monkeypatch.setattr(entrypoint, "read_json_input", MagicMock(side_effect=failure))
    elif failure_at == "scrape":
        pipeline.scraper.scrape_all.side_effect = failure
    elif failure_at == "process":
        pipeline.processor.process.side_effect = failure
    elif failure_at == "model":
        pipeline.llm.enrich.side_effect = failure
    elif failure_at == "save":
        monkeypatch.setattr(entrypoint, "write_json_output", MagicMock(side_effect=failure))
    else:
        pipeline.exporter.export.side_effect = failure

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert exc.value.__context__ is failure
    output = pipeline.output_file.read_text(encoding="utf-8")
    assert f"failure_stage={expected_stage}\n" in output
    assert "failure_reason=" in output
    assert len(output.splitlines()) == 2
    assert "private-value" not in output
    assert "private.invalid" not in output


@pytest.mark.parametrize("marker", ["/account/access", "Cloudflare", "X requires login"])
def test_x_verification_failure_has_an_actionable_safe_reason(pipeline, marker):
    pipeline.scraper.scrape_all.side_effect = RuntimeError(f"{marker} token=private-value")

    with pytest.raises(SystemExit):
        entrypoint.main()

    output = pipeline.output_file.read_text(encoding="utf-8")
    assert "failure_stage=X 抓取\n" in output
    verification = output.index("请账号负责人先在浏览器完成验证")
    maintainer = output.index("完成后请通知项目维护人")
    session_update = output.index("由维护人更新自动抓取的登录状态")
    recovery = output.index("并确认恢复")
    assert verification < maintainer < session_update < recovery
    assert "确认能正常查看趋势页" in output
    assert "仅完成浏览器验证，还不能视为任务已恢复" in output
    assert "X_STORAGE_STATE_B64" not in output
    assert "X_COOKIES" not in output
    assert "retry_from_run" not in output
    assert "private-value" not in output
    pipeline.exporter.export.assert_not_called()


def test_success_does_not_emit_failure_outputs(pipeline):
    entrypoint.main()

    assert not pipeline.output_file.exists()
    pipeline.exporter.export.assert_called_once()


def test_model_failure_directs_api_configuration_recovery_to_maintainer(pipeline):
    pipeline.llm.enrich.side_effect = RuntimeError("private-api-key=private-value")

    with pytest.raises(SystemExit):
        entrypoint.main()

    output = pipeline.output_file.read_text(encoding="utf-8")
    assert "LLM API 调用异常" in output
    assert "项目维护人" in output
    assert "Actions Secrets" in output
    assert "必要时更换可用 API" in output
    assert "补跑本次日报" in output
    assert "private-value" not in output


@pytest.mark.parametrize("github_output_unavailable", ["unset", "unwritable"])
def test_metadata_failure_does_not_replace_pipeline_failure(
    pipeline, monkeypatch, github_output_unavailable
):
    failure = RuntimeError("X access failed")
    pipeline.scraper.scrape_all.side_effect = failure
    if github_output_unavailable == "unset":
        monkeypatch.delenv("GITHUB_OUTPUT")
    else:
        pipeline.output_file.mkdir()

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert exc.value.__context__ is failure
    pipeline.exporter.export.assert_not_called()
