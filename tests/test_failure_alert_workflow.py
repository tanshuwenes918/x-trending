"""Exercise the dependency-free Actions alert with a mocked GitHub/Feishu runtime."""

import hashlib
import hmac
import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github/workflows/daily-trending.yml"
WEBHOOK = "https://invalid.example/hooks/private-webhook-token"
SIGNING_SECRET = "private-signing-secret"
RUN_ID = 123456789

NODE_HARNESS = r"""
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const key of ['FEISHU_WEBHOOK_URL', 'FEISHU_SECRET', 'FAILED_STAGE',
                   'FAILED_REASON', 'IS_ALERT_TEST', 'GITHUB_RUN_ATTEMPT']) {
  delete process.env[key];
}
Object.assign(process.env, input.env);
const observed = {fetchCalls: [], jobQueries: [], infos: [], warnings: [], failures: []};
const jobs = input.jobs || [
  {name: 'unrelated', conclusion: 'failure', steps: [
    {name: 'UNRELATED_FAILURE_MUST_NOT_BE_USED', conclusion: 'failure'}
  ]},
  {name: 'scrape', conclusion: 'failure', steps: [
    {name: 'Checkout', conclusion: 'success'},
    {name: 'Install dependencies', conclusion: 'failure'}
  ]}
];
const github = {rest: {actions: {
  listJobsForWorkflowRunAttempt: async args => {
    observed.jobQueries.push(args);
    if (input.scenario === 'github_error') throw new Error('API unavailable');
    return {data: {jobs}};
  }
}}};
const context = {repo: {owner: 'example-owner', repo: 'example-repo'},
  serverUrl: 'https://github.com', runId: 123456789, runNumber: 42};
const core = {
  info: message => observed.infos.push(String(message)),
  warning: message => observed.warnings.push(String(message)),
  error: message => observed.warnings.push(String(message)),
  setFailed: message => observed.failures.push(String(message)),
};
global.fetch = async (url, options) => {
  observed.fetchCalls.push({url, body: JSON.parse(options.body)});
  if (input.scenario === 'network_error') {
    throw new Error('Request timed out at ' + input.env.FEISHU_WEBHOOK_URL);
  }
  const body = input.scenario === 'business_error'
    ? {code: 19021, msg: input.env.FEISHU_WEBHOOK_URL + ' ' + input.env.FEISHU_SECRET}
    : input.scenario === 'missing_ack' ? {msg: 'unrecognized response'}
    : input.scenario === 'legacy_ack' ? {StatusCode: 0, StatusMessage: 'success'}
    : {code: 0, msg: 'success'};
  return {
    ok: input.scenario !== 'http_error',
    status: input.scenario === 'http_error' ? 500 : 200,
    json: async () => {
      if (input.scenario === 'invalid_json') {
        throw new Error(input.env.FEISHU_WEBHOOK_URL);
      }
      return body;
    },
    text: async () => input.scenario === 'invalid_json'
      ? input.env.FEISHU_WEBHOOK_URL : JSON.stringify(body),
  };
};
(async () => {
  try {
    const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    await new AsyncFunction('require', 'github', 'context', 'core', input.script)(
      require, github, context, core);
  } catch (error) {
    observed.thrown = {name: error.name, message: error.message};
  }
  process.stdout.write(JSON.stringify(observed));
})();
"""


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def alert_script(workflow):
    steps = workflow["jobs"]["notify_failure"]["steps"]
    return next(step["with"]["script"] for step in steps if "github-script@" in step.get("uses", ""))


def run_alert(script, *, scenario="success", env=None, jobs=None):
    node = shutil.which("node")
    assert node, "Node.js is required to test the Actions alert script"
    variables = {
        "FEISHU_WEBHOOK_URL": WEBHOOK,
        "FEISHU_SECRET": SIGNING_SECRET,
        "GITHUB_RUN_ATTEMPT": "3",
        "IS_ALERT_TEST": "false",
    }
    variables.update(env or {})
    completed = subprocess.run(
        [node, "-e", NODE_HARNESS],
        input=json.dumps({"script": script, "scenario": scenario, "env": variables, "jobs": jobs}),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return json.loads(completed.stdout)


def payload(result):
    assert len(result["fetchCalls"]) == 1
    return result["fetchCalls"][0]["body"]


def visible_logs(result):
    return json.dumps({key: result.get(key) for key in ("infos", "warnings", "failures", "thrown")})


def test_alert_runs_on_an_independent_job_only_after_failure(workflow):
    notifier = workflow["jobs"]["notify_failure"]
    assert notifier["needs"] in ("scrape", ["scrape"])
    condition = notifier["if"].replace(" ", "")
    assert "always()" in condition
    assert "needs.scrape.result=='failure'" in condition
    assert not any("checkout@" in step.get("uses", "") for step in notifier["steps"])


def test_manual_alert_test_stops_before_checkout_or_scraping(workflow):
    triggers = workflow.get("on", workflow.get(True))
    option = triggers["workflow_dispatch"]["inputs"]["test_failure_alert"]
    assert option["type"] == "boolean" and option["default"] is False
    first_step = workflow["jobs"]["scrape"]["steps"][0]
    assert "inputs.test_failure_alert" in first_step["if"]
    assert "exit 1" in first_step["run"]


def test_alert_contains_safe_stage_reason_and_direct_run_link(alert_script):
    result = run_alert(alert_script, env={
        "FAILED_STAGE": "模型摘要",
        "FAILED_REASON": "模型服务调用失败，请检查 API 配置及任务日志。",
    })
    message = payload(result)
    text = json.dumps(message, ensure_ascii=False)
    assert "模型摘要" in text
    assert "模型服务调用失败" in text
    assert f"https://github.com/example-owner/example-repo/actions/runs/{RUN_ID}" in text
    assert not result["failures"] and not result.get("thrown")


def test_failure_step_lookup_uses_current_attempt_and_scrape_job(alert_script):
    result = run_alert(alert_script)
    assert result["jobQueries"]
    assert result["jobQueries"][0]["attempt_number"] == 3
    assert result["jobQueries"][0]["run_id"] == RUN_ID
    text = json.dumps(payload(result), ensure_ascii=False)
    assert "Install dependencies" in text or "依赖" in text
    assert "UNRELATED_FAILURE_MUST_NOT_BE_USED" not in text


def test_github_lookup_failure_still_sends_generic_alert(alert_script):
    result = run_alert(alert_script, scenario="github_error")
    text = json.dumps(payload(result), ensure_ascii=False)
    assert f"/actions/runs/{RUN_ID}" in text
    assert not result["failures"] and not result.get("thrown")


def test_feishu_signature_matches_existing_exporter_algorithm(alert_script):
    body = payload(run_alert(alert_script))
    timestamp = str(body["timestamp"])
    expected = hmac.new(
        f"{timestamp}\n{SIGNING_SECRET}".encode(), b"", hashlib.sha256,
    ).digest()
    assert body["sign"] == base64.b64encode(expected).decode()


def test_unsigned_webhook_does_not_require_a_signing_secret(alert_script):
    result = run_alert(alert_script, env={"FEISHU_SECRET": ""})
    body = payload(result)
    assert "sign" not in body and "timestamp" not in body
    assert not result["failures"] and not result.get("thrown")


def test_legacy_feishu_success_acknowledgement_is_accepted(alert_script):
    result = run_alert(alert_script, scenario="legacy_ack")
    payload(result)
    assert not result["failures"] and not result.get("thrown")


def test_intentional_test_alert_is_clearly_labeled(alert_script):
    result = run_alert(alert_script, env={"IS_ALERT_TEST": "true"})
    body = payload(result)
    assert "测试" in body["content"]["post"]["zh_cn"]["title"]


@pytest.mark.parametrize("scenario", ["http_error", "business_error", "network_error", "invalid_json", "missing_ack"])
def test_delivery_failure_is_reported_without_secrets_or_blind_retries(alert_script, scenario):
    result = run_alert(alert_script, scenario=scenario)
    assert len(result["fetchCalls"]) == 1
    assert result["failures"] or result.get("thrown")
    logs = visible_logs(result)
    assert WEBHOOK not in logs
    assert SIGNING_SECRET not in logs


def test_missing_webhook_fails_without_attempting_network(alert_script):
    result = run_alert(alert_script, env={"FEISHU_WEBHOOK_URL": ""})
    assert not result["fetchCalls"]
    assert result["failures"] or result.get("thrown")
