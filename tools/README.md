# tools/

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `build_index.py` | **主程序**：读 `cases/<id>/<run>/{request,body,meta}.json`（+可选 `prefill_attr/`）→ 生成单文件 `index.html` | 无（纯标准库） |
| `make_demo.py` | 生成 `demo/` 下 3 个合成 case | 无 |
| `repro_judge.py` | 合格判据：边界下标 / 边界 token 形态 / 标记是否被消费 / 是否可见切断 | 无 |
| `capture_cases.py` | 抓 chat 响应（带 logprobs），存成上表结构 | `TOKEN_BASE` `PROBE_KEY` |
| `capture_tokens.py` | 抓 `/generate` 原生 `output_ids`（判定 special vs 文本的决定性证据） | 同上 |
| `capture_align.py` | 对齐 logprobs 与交付字段，做字符级 diff | 同上 |
| `hunt_repro.py` | 反复重跑找"合格"运行（可要求自然结束） | 同上 |
| `roll_case.py` | 批量 roll + 症状统计（词中断 / content 空 / 尾部退化 / 标记载荷） | 同上 |
| `build_prefill_attr.py` | 把 `prefill/*.json` 变成逐 token 溯源（请求内容 / 工具 / 模板注入） | 需先跑 `rt_render_remote.py` |
| `rt_render_remote.py` | 在**模型机器**上用真模板 + tokenizer 渲染 prompt（返回 ids/offsets/词表） | ssh + `MODEL_PATH` |
| `probe_cache_reuse_modes.py` | 判定"生成的 token 能否被前缀缓存复用" | `TOKEN_BASE` `PROBE_KEY` |
| `probe_injection3.py` | 控制符注入 A/B（真控制符 vs 纯文本同义） | 同上 |

环境变量：

```bash
export TOKEN_BASE="http://127.0.0.1:30000"   # OpenAI 兼容端点
export PROBE_KEY="sk-..."                    # API key（不会落盘）
export MODEL_PATH="/path/to/model"           # 仅 prefill 渲染需要
export REMOTE_HOST=127.0.0.1 REMOTE_PORT=22  # 仅 rt_render_remote 需要
```

`build_index.py` 的输入约定：

```
<cwd>/cases/<case_id>/<run>/{request.json, body.json, meta.json}
<cwd>/prefill_attr/<case_id>.json     # 可选，⑨ tab
<cwd>/tokens/<slug>/                  # 可选，④⑤ tab
```

```bash
python3 tools/build_index.py                                   # → ./index.html
DATA_DIR=/path/to/data OUT_HTML=/tmp/a.html python3 tools/build_index.py
```

来源标签：chat 与原生 token 采集可设置 INFERENCE_SOURCE；构建时读取运行 meta.json 的 inference_source，或案例索引的同名默认值。未标注时回退到 endpoint 的源站，再回退到“未标注来源”。左侧筛选按案例计数，同一案例每个来源保留一份完整响应。

每次实验可选 env.json / flags.json（JSON 对象），或 meta.json 的 deployment_env / deployment_flags。build_index.py 为每个运行显示 Env / Flags、下载和跨实验差异。capture_cases.py / capture_tokens.py 通过 DEPLOYMENT_ENV_FILE / DEPLOYMENT_FLAGS_FILE 在请求前保存脱敏配置；不自动收集客户端环境。runtime_config_note 用于说明配置记录来源。未提供配置时保存空对象。

逐次对照需要每个运行都可查看完整请求/响应时，在对应 meta.json 设置 `"embed_full_response": true`。默认仍只内嵌各来源的代表运行以控制页面大小；Env / Flags 始终按每次运行保留。
