# LLM Token Stream Viewer / 推理 token 流溯源查看器

把一个 chat completion 的**逐 token 流**渲染成可交互的单文件网页，用来回答这类问题：

- 这次响应的 `reasoning_content` / `content` 是在**第几个 token** 切开的？被切掉的是什么？
- 输出里的 `</think>` 到底是**一个 special token**，还是**模型手写的 3 个文本 token**？
- 输入（prefill）里每个 token 来自**请求内容**还是 **chat template 注入**？哪些是 special token？
- 边界落在引号/代码块中间时，字段拼接起来是否缺字符？

> 单文件、离线可用：`index.html` 内嵌全部数据，双击就能看（不需要联网、不需要起服务）。

![demo](docs/screenshot.png)

## 快速开始（30 秒）

```bash
git clone <this repo> && cd <this repo>
open demo/index.html          # macOS；Linux 用 xdg-open，Windows 双击
```

`demo/index.html` 里有 3 个合成 case（**不含任何真实数据**）：

| demo case | 演示什么 |
|---|---|
| `01_special_token_boundary` | 健康样本：边界是**单个 special token**，无损 |
| `02_text_form_in_quote` | **核心 bug**：思考里引用 `` `</think>` ``（3 个文本 token）被解析器当成结束符 → 思考被截短、其余漏进 `content` |
| `03_marker_residue_in_content` | 正文里原样交付了一个 special 形态的 `</think>` |

在 `02` 里点 **③ token 流** → 按 **「🔍 标出 </think>」**，会显示：

```
标记：共 1 处 ｜ </think> 1 处（文本形态 1）
#19-21 </think>（3 个 token）
```

—— 一眼看出"它不是一个 special token，而是三个文本 token"。

## 页面结构

左侧条目 → 右侧 9 个 tab：

| tab | 内容 |
|---|---|
| ① 请求 / prompt | 发出去的 JSON ↔ **模型实际看到的 prompt**（模板渲染后，按来源着色） |
| ② 响应 | 交付的 `reasoning_content` / `content` / `tool_calls` |
| ③ token 流 | 逐 token 网格（字段归属、边界 token 划掉、**一键标出 `</think>`**、悬停看 id/字节） |
| ④ /generate 原始 token | 同一 prompt 走 sglang 原生接口的 token id（判定 special vs 文本的决定性证据） |
| ⑤ 输入 token 网格 | prompt 的 token 化 |
| ⑥ 判定 · 丢弃 · 对照 | 标记是 special 还是文本？拼接后被丢了什么？ |
| ⑦ 接缝 | 边界两侧原文拼接对照 |
| ⑧ 头部 | HTTP 头 |
| ⑨ 输入 prefill 溯源 | 每个 token 来自「请求内容 / 工具定义 / chat template 注入」，以及 special/文本 |

## 按推理来源查看

左侧 **Source · 推理来源** 显示各来源的案例数，可筛选案例；每个案例和当前运行也显示来源。
同一案例包含不同来源的运行时，选定来源后只展示该来源的运行，并保留每个来源的一份完整响应。

在每个运行的 meta.json 中设置：

    {"case_id": "example", "run": "run1", "inference_source": "实验部署 A"}

也可在 kit/queries/index.json 的案例条目设置 inference_source 作为该案例的默认来源。
chat 和原生 token 采集脚本支持 INFERENCE_SOURCE 环境变量；没有标签时使用 meta.json 的
endpoint URL 源站（协议、主机、端口，去除账号、路径、查询参数与 fragment）。旧数据没有这些字段时显示“未标注来源”，仍可正常打开。
原始文件位置 source 字段不作为推理来源，避免把文件路径误当部署名称。仅设置显示标签不会改变采集目标。

例如，同一个 case 的 run1 标记“部署 A”、run2 标记“部署 B”，选择“部署 B”后，
请求、响应和 logprobs 来自 run2。原生 /generate token 数据仅在其来源匹配时显示。

## 每次实验的 Env / Flags

每个运行的 Env / Flags 页保存并展示两栏配置，可下载 JSON，选择另一实验并排对比，
或勾选“只看已记录的取值差异”。配置随运行切换，不使用其他运行的设置。
黄色表示双方已记录且取值不同；缺项显示“—”，不把没有记录误当成配置相同或不同。

可选输入放在 cases/<case>/<run>/ 或 tokens/<slug>/：

    env.json     {"CUDA_VISIBLE_DEVICES": null, "EXAMPLE_CACHE_DIR": "/cache"}
    flags.json   {"--attention-backend": "example", "--disable-cuda-graph": true}

也支持 meta.json 中的 deployment_env / deployment_flags 对象；同名 JSON 文件优先。
环境变量的 null 表示显式 unset；Flags 中的 null 表示未设置。
非自部署和没有历史配置的实验可以不提供这两个文件，两栏保持空记录。
meta.json 的 runtime_config_note 可注明“启动脚本显式设置”“启动日志生效参数（含默认值）”
或其他来源与限制，避免把重建值当作原始命令行。

采集脚本可在请求前保存显式提供的配置快照：

    export DEPLOYMENT_ENV_FILE=/path/to/server-env.json
    export DEPLOYMENT_FLAGS_FILE=/path/to/server-flags.json

这些文件应描述推理服务；脚本不会读取采集客户端的完整环境。没有提供文件时写入空对象，
以免复用输出目录时残留前一实验的配置。保存和渲染时会遮挡常见凭据字段、Bearer 值及 URL 的账号和签名查询参数。
真实部署配置与真实抓取数据一并留在本地，仓库示例仅使用合成数据。

## 背后的结论（这套工具查出来的）

详见 [`docs/mechanism.md`](docs/mechanism.md)。要点：

1. **`reasoning_content`/`content` 的切分是纯字符串匹配**：`text.split("</think>", maxsplit=1)`（非流式）与
   `text.find("</think>")`（流式）。而 `</think>` 是 **added token 但 `special=false`** ——
   detokenize 后与模型手写的 3 个文本 token **在文本层面完全无法区分** ⇒ 引号里的引用也会被当成结束符。
2. 实测 6 条真实 case：**真边界 6/6 都是单个 special token**，其中 4 条被更早的"引用"截胡 ⇒
   **隐藏思考被当成可见正文**（最长一条 32,563 字）。
3. 修法：把切分点从"字符串"改为"token 身份"（只认 special token 的 id），并保留"无 special 时回退字符串规则"。
4. 输入侧：消息正文里的 `<|user|>` / `</think>` 等**会被 tokenizer 匹配成真控制符**（不是普通文本）；
   所以**不可信内容里的控制符需要在网关层中和**（`</think>` 因为 `special=false`，`split_special_tokens` 拆不开它）。

## 用在你自己的端点上

```bash
export TOKEN_BASE="http://127.0.0.1:30000"     # 你的 OpenAI 兼容端点
export PROBE_KEY="sk-..."                      # API key（不会写进任何文件）
export MODEL_PATH="/path/to/model"             # 仅 ⑨ prefill 溯源需要
```

抓一条响应（含 logprobs）放进 `cases/<id>/<run>/`：

```
cases/<id>/<run>/request.json   # 原样发出去的请求体
cases/<id>/<run>/body.json      # 原样收到的响应体（必须带 logprobs）
cases/<id>/<run>/meta.json      # {"case_id":..., "run":"...", "temperature":0, ...}
```

然后：

```bash
python3 tools/build_index.py                       # → index.html
DATA_DIR=. OUT_HTML=index.html python3 tools/build_index.py
```

`tools/` 里还有本次排查用到的全部脚本：

| 脚本 | 用途 |
|---|---|
| `capture_cases.py` / `capture_tokens.py` | 抓 chat 响应 / `/generate` 原生 token id |
| `hunt_repro.py` / `roll_case.py` | 反复重跑找"可复现"的运行 + 症状统计（词中断、content 空、尾部退化…） |
| `repro_judge.py` | 合格判据（可复核的自动判定） |
| `build_prefill_attr.py` + `rt_render_remote.py` | 用真模板渲染 prefill 并逐 token 溯源（需能 ssh 到模型机器） |
| `probe_cache_reuse_modes.py` | 判定"生成的 token 能不能被前缀缓存复用"（决定 retokenization 漂移有无缓存代价） |
| `probe_injection3.py` | 控制符注入 A/B 实验（真控制符 vs 纯文本同义） |
| `make_demo.py` | 生成上面那 3 个合成 demo |

## 注意

- **本仓库不含任何真实流量**：demo 是合成的；真实 case 的原始数据没有上传。
- 抓取时 **`temperature` 很关键**：高温会自己造出"词沙拉 / 半句截断"，看起来像 bug 其实不是。页面里对 `temperature > 1.05` 的运行会打 **⚠️ 高温度变体** 警示。
- `</think>` 这类 added token 的行为容易误判：`skip_special_tokens=True` **不会清**它、`split_special_tokens=True` **也拆不开**它（因为它 `special=false`）。

## 验证

    python3 -m unittest discover -s tests -v

来源测试使用合成响应，覆盖运行与案例的标签优先级、旧数据兼容、endpoint 源站脱敏，
以及同一案例不同来源的完整请求和响应保留；不访问推理端点。

## License

MIT
