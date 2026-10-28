# X Trending

通过 GitHub Actions 抓取 X 全球趋势，生成面向 AI 音乐、AI 视频增长运营的中文日报，并推送到飞书群。运行失败时，向同一个飞书机器人发送告警，方便及时发现和处理。

## 当前运行方式

- 定时配置：每天北京时间 **09:00**（UTC `01:00`）。这是计划触发时间，GitHub 排队可能使实际启动延后。
- 默认范围：全球趋势首页和 24 个来源分类，筛选最近 24 小时的推文。
- 日报内容：先列出最多 7 条首页热点，按首页展示顺序保留；其余内容合并为 6 个运营分组，去重并按互动热度选取每组最多 7 条，另生成最多 5 条运营建议。首页已选热点不在分类中重复展示，分类会补位；实际数量取决于可用内容。
- 纯图片、视频帖也会保留。当前摘要接口只读取正文和可用的媒体替代描述，不分析画面；无正文、无有效描述时，会标明需要查看原帖，不猜测事件内容。这类条目及未分类首页热点不推荐引流动作。
- 模型接口：兼容 Responses 和 Chat Completions。
- 输出：正常定时运行推送飞书，并保存 JSON；任务失败则触发独立的飞书告警任务。

普通定时运行、未填写 `retry_from_run` 的手动运行，都会重新抓取最新数据。只有明确填写旧任务 ID 时才尝试复用旧数据，不会自动拿某一天的数据连续生成后续日报。

## 日常使用

### 手动生成日报

1. 打开 [Daily X Trending](https://github.com/tanshuwenes918/x-trending/actions/workflows/daily-trending.yml)。
2. 点击 **Run workflow**，分支选择 `main`。
3. 按下表选择参数，再点击运行。
4. 点击新出现的历史任务查看进度。成功时，确认群内日报或下载任务附件；失败时，查看群内告警和任务日志。

| 参数 | 默认值 | 使用说明 |
| --- | --- | --- |
| `countries` | `Global` | 普通运行保留默认值；其他地区是否可用取决于 X 页面支持情况。 |
| `categories` | 24 个来源分类 | 普通运行保留默认值；自定义时用英文逗号分隔配置中的分类名。 |
| `output_format` | `feishu` | `feishu` / `both` 可推飞书；`json` 只保存数据；`preview` 还会在日志显示文本预览。正常完成时均保存 JSON。 |
| `dry_run` | 不勾选 | 勾选后跳过日报推送。只想验证时，选择 `json` 并勾选此项。**失败告警仍会发送。** |
| `retry_from_run` | 留空 | 留空抓取最新数据；仅在复用失败任务保存的数据时填写，见下文。 |
| `test_failure_alert` | 不勾选 | 只测试失败告警，普通运行保持关闭。 |

需要正式推送时，使用 `output_format=feishu` 或 `both`，并保持 `dry_run` 不勾选。

### 找到任务 ID 和保存的数据

在历史列表点击某次 **Daily X Trending**，浏览器地址类似：

```text
https://github.com/tanshuwenes918/x-trending/actions/runs/123456789/job/987654321
```

任务 ID 是 `/actions/runs/` 后面的 **`123456789`**，不是 `/job/` 后面的数字，也不是页面中的 `#160` 之类的序号。

在该任务的 **Summary → Artifacts → x-trending-output** 下载保存的数据：

| 文件 | 含义 | 能否填写该任务 ID 补跑 |
| --- | --- | --- |
| `retry_input_*.json` | 已抓取、筛选的非空数据，模型生成失败时保存 | 可以，重新调用模型，不再抓取 X。 |
| `x_trending_*.json` | 已完成模型处理的日报，飞书发送前保存 | 当前 `retry_from_run` 不支持直接补发此文件，请交给维护人处理。 |
| `raw_candidates_*.json` | 筛选前的完整抓取候选，供排查漏项；完整日报中的 `processing_diagnostics` 记录入选或排除原因 | 不能直接填任务 ID 补跑，由维护人排查使用。 |
| 没有附件或没有上述文件 | 可能在登录、抓取等阶段就失败，或附件未成功上传 | 不能仅靠任务 ID 恢复，应修复后重新抓取。 |

附件保留时间由仓库的 GitHub Actions 设置决定，过期或被删除后不能下载；需长期保留时请提前下载。

### 模型失败后复用数据

由维护人修复模型配置后，先确认原失败任务的附件中包含 `retry_input_*.json`，再新建一次 **Run workflow**：

1. 在 `retry_from_run` 填入原失败任务 ID。
2. 要直接生成并推送，选择 `both` 或 `feishu`，不勾选 `dry_run`。
3. 要先验证，选择 `json` 并勾选 `dry_run`；验证成功后的完整日报如需原样补发，交由维护人处理，不要把这个成功任务的 ID 当作失败数据补跑。

补跑保留原数据的采集时间，生成的是那次采集对应的日报。要获取今天的新内容，必须留空 `retry_from_run`。

**Re-run jobs 会沿用原任务的输入参数。** 要切换成抓取最新内容，请新建一次 Run workflow，并确认 `retry_from_run` 留空。

## 收到失败告警后

失败通知会包含北京时间、失败环节、排查提示、任务 ID、尝试次数和可点击的日志链接。成功运行不会发送失败告警。每次失败的运行尝试都会触发告警，手动重复失败也可能再次收到通知。

| 失败环节 | 账号负责人 / 群内同事 | 项目维护人 |
| --- | --- | --- |
| X 需要重新登录、解锁或验证 | 用抓取账号在浏览器完成验证，确认趋势页正常，再通知维护人 | 导出最新登录会话，更新 Actions Secrets，重新抓取并确认恢复。 |
| 模型摘要 / LLM API 调用异常 | 通知维护人处理模型接口 | 检查服务状态、额度和模型配置，及时更新 Actions Secrets，必要时更换可用 API，再补跑。 |
| 飞书推送未确认成功 | 先检查群内是否已有日报，避免重复补发 | 检查机器人配置、签名及发送日志，确认实际送达情况后再处理。 |
| 安装依赖、测试、保存或上传附件等失败 | 将告警链接交给维护人 | 打开对应任务日志排查；如果日报已经发出，不要重复发送。 |

**仅在浏览器完成 X 验证，不会自动更新 Actions 的登录状态，也不能视为自动任务已经恢复。** Cookies 和 storage state 的导出、更新及后续验证由维护人负责，群内通知不作为配置操作教程。

通知使用独立任务，不依赖抓取任务完成 checkout 或安装 Python 依赖；通知任务本身失败时，也应查看 Actions。人工取消的运行不触发该失败通知。若 GitHub 没有启动任务、平台不可用，或飞书机器人本身无法访问，这套机制不能保证告警送达。

### 测试告警

维护人可在 **Run workflow** 勾选 `test_failure_alert`。任务会在 checkout 前故意失败，随后发送一条明确标注“测试”的飞书告警，不抓取 X、不调用模型、不发送日报。

测试运行显示红色是预期结果。请检查其中的 **notify_failure** 任务是否成功；日志中的 `Failure alert sent to Feishu successfully.` 表示飞书接口已确认成功。正常运行时不要勾选此项。

## 维护人：配置与恢复

### Actions Secrets

在仓库 **Settings → Secrets and variables → Actions → Secrets** 管理以下配置：

| Secret | 用途 |
| --- | --- |
| `X_STORAGE_STATE_B64` | 推荐的 X 登录配置：Playwright storage state JSON 的 Base64 内容。 |
| `X_COOKIES` | 没有配置 storage state 时使用的 X Cookie 请求头；若保留此项，换号或刷新会话时也同步更新。 |
| `LLM_API_KEY` | 模型服务 API Key。 |
| `LLM_BASE_URL` | 模型服务基础地址。 |
| `LLM_MODEL` | 服务支持的准确模型 ID。 |
| `FEISHU_WEBHOOK_URL` | 日报和失败告警共用的飞书机器人地址。 |
| `FEISHU_SECRET` | 可选；机器人开启签名校验时填写。 |

X 登录配置至少需要一种。**`X_STORAGE_STATE_B64` 优先于 `X_COOKIES`**：同时配置时，旧 Cookie 请求头会被跳过，不会混入 storage state。仅更新 `X_COOKIES` 无法修复一个已经失效的 storage state。

不要将 API Key、Cookie、登录状态、机器人地址放进仓库、普通 Variables、截图或群消息中。

### 恢复 X 登录会话

1. 由账号负责人用**实际抓取账号**打开 [全球趋势页](https://x.com/i/jf/global-trending/home)，完成登录、解锁或验证，确认能看到推文和分类按钮。
2. 由维护人从这个可用的浏览器会话重新导出登录状态，更新 `X_STORAGE_STATE_B64`。若保留 `X_COOKIES`，一并更新为同一账号、同一会话的新 cookies，避免遗留旧号配置。
3. 新建一次 Actions 运行，`retry_from_run` 留空，先用 `output_format=json`、`dry_run=true` 验证完整抓取和模型生成。
4. 确认任务成功和结果日期后，再处理日报推送。仅本机浏览器可用，不能证明 Actions 也能正常访问。

Chrome 和 Edge 中已验证的登录会话，都可整理成 Playwright storage state 使用。**现有导出脚本不会直接读取普通 Chrome / Edge 已有的登录状态，也没有 Edge 参数。** 若普通 Edge 可用而脚本窗口始终被验证拦截，应由维护人从 Edge 的可用会话另行导出，不能只更换浏览器名称就认定恢复。

仓库自带的导出方式需要先完成下方本地环境配置，在项目根目录运行：

```bash
python scripts/export_x_storage_state.py --persistent
```

`--persistent` 使用已安装的 Google Chrome，但会打开独立的 `outputs/x_playwright_profile`，需要在这个窗口完成登录；该专用 profile 可在以后继续使用。省略此参数则打开新的 Playwright Chromium 会话，同样需要登录。两种方式都不复用日常浏览器 profile。

登录完成后在终端按 Enter，脚本会重新打开趋势页并检查可访问性。仍处于登录页、账号验证页、Cloudflare 验证或加载超时时，导出失败，不替换已有会话文件。成功时生成：

- `outputs/x_storage_state.json`
- `outputs/x_storage_state.b64.txt`

将 `.b64.txt` 的完整内容更新到 `X_STORAGE_STATE_B64`。已安装并登录 GitHub CLI 的 macOS / Linux 维护人也可执行：

```bash
gh secret set X_STORAGE_STATE_B64 --repo tanshuwenes918/x-trending < outputs/x_storage_state.b64.txt
```

脚本不另行生成 `X_COOKIES` 请求头；需同步此备用配置时，由维护人从同一会话导出。JSON、Base64 文件和浏览器 profile 都包含登录凭据，请保留在本机；`outputs/` 已被 Git 忽略。

X 会话可能失效，也可能因账号或运行环境被要求再次验证。本地导出成功不能保证云端长期不再触发验证；仍需以实际 Actions 运行结果为准。

### 更换或恢复模型 API

维护人在 Actions Secrets 中检查并按需更新 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，确保三项来自匹配的服务和模型配置。修改本地 `.env` 不会更新 Actions Secrets。

- `LLM_BASE_URL` 填服务基础地址，例如 `https://api.example.com` 或 `https://api.example.com/v1`，不要填写完整的 `/responses` 或 `/chat/completions` 路径。
- 客户端优先调用 Responses；该请求返回 HTTP 404 时，尝试同一服务的 Chat Completions。明确的 `model_not_found` 错误不切换协议。
- 换回支持 Responses 的服务不需要改代码。支持流式响应，也能读取兼容服务返回的完整 JSON。
- 鉴权失败、限流、过载、超时或输出不完整仍会导致任务失败；协议兼容不等于自动重试或自动更换服务。

模型失败且已有有效输入时，程序尝试保存 `retry_input_*.json`，并上传为任务附件。配置恢复后，按上文“模型失败后复用数据”操作。登录或抓取阶段就失败、没有可用数据时，不能靠补跑 ID 恢复。

## 本地开发与验证

Actions 使用 Python 3.11。需要本地运行时，先准备 Python 3.11 和依赖；运行完整测试还需要 Node.js 20 或更新版本，用于模拟 Actions 告警脚本。

macOS / Linux：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

在本机 `.env` 填入登录状态及模型配置。例如：

```dotenv
X_COUNTRIES=Global
X_EXPLORE_URL=https://x.com/i/jf/global-trending/home
X_STORAGE_STATE_B64=<最新登录状态的完整Base64>
X_COOKIES=

LLM_ENABLED=true
LLM_REQUIRED=true
LLM_API_KEY=<API Key>
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=<服务支持的模型ID>
LLM_REQUEST_TIMEOUT=300

OUTPUT_FORMAT=preview
DRY_RUN=true
TIMEZONE=Asia/Shanghai
```

新抓取并生成预览，不发送飞书：

```bash
python main.py --output-format preview --dry-run
```

复用最新的本地完整日报，重新生成模型摘要并预览：

```bash
python main.py --input-json latest --output-format preview --dry-run
```

复用模型失败时保存的输入，重新生成摘要并预览：

```bash
python main.py --input-json "outputs/retry_input_<timestamp>.json" --output-format preview --dry-run
```

将 `<timestamp>` 替换为实际保存文件的时间戳。

**`--input-json` 只跳过 X 抓取，仍会调用模型；不代表原样查看或原样补发。** 输入报告的采集时间会保留，完成后按该时间写入输出，因此可能覆盖同名本地 JSON；需保留原版时先备份。

要在本地生成并发送飞书，先配置 `FEISHU_WEBHOOK_URL` 和所需的 `FEISHU_SECRET`，并将 `.env` 中 `DRY_RUN` 改为 `false`，再运行：

```bash
python main.py --input-json latest --output-format feishu
```

这条命令也会重新调用模型。`--output-format feishu` 不会覆盖 `.env` 中的 `DRY_RUN=true`。已完成日报的原样补发应由维护人单独处理；当前 Actions 表单没有“仅发送成功日报”的入口。

运行测试，不访问真实 X、模型或飞书服务：

```bash
python -m pytest -q tests
```

本地运行 `main.py` 失败时只记录日志并退出；自动群告警由 Actions 工作流负责，不是本地脚本的功能。

## 数据与分组

流程为：登录趋势页 → 逐类抓取 → 按时间筛选和去重 → 热度排序与分组 → 模型摘要及运营建议 → 保存 JSON → 推送飞书。抓不到任何推文或筛选后完全没有可用内容时，任务失败，不发送空日报。

| 运营分组 | 来源分类 |
| --- | --- |
| AI / Tech / Creator Tools | Technology、Science、Business & Finance、cryptocurrency |
| Music / Dance / Entertainment | music、dance、celebrity、Movies & TV、anime |
| Viral Culture / Meme / Social Buzz | meme、relationship、fashion、beauty、food、Pets |
| Gaming / Sports / Youth Culture | Gaming、Sports、cars |
| Lifestyle / Outdoor / Travel | Travel、Nature & Outdoors、Health & Fitness、Home & Garden |
| News / Society / Sensitive Topics | News、religion |

每条推文保留 `author`、`created_at`、`content`、`replies`、`retweets`、`likes`、`views`、`media_urls`、`tweet_url`，处理后添加 `country`、`source_category`、`trending_term`、`score`、`summary`。

日报还记录 `timestamp`、`timezone`、`lookback_hours`、`groups`、`top_actions`、统计 `summary` 和 `llm_used`。飞书展示中文摘要、可用的互动数与原推文链接；未采集到的指标不展示为零值。
