# 机制说明：为什么 `</think>` 会被误判为"思考结束"

（本文只讲机制与可复现的判据，不含任何真实流量或内部地址。）

## 1. 现象

多轮 agent 会话里，模型的 `reasoning_content` 有时异常短，而 `content` 异常长，且 `content` 的开头
像是"思考的后半句"。极端情况下，一条 3 万字的内部独白被当成 assistant 的可见回复交付出去。

## 2. 根因：字符串匹配 vs token 身份

sglang（`sglang/srt/parser/reasoning_parser.py`）里：

```python
# Glm45Detector.__init__
super().__init__("<think>", "</think>", tool_start_token="<tool_call>", ...)
#                            ^^^^^^^^^^ 纯字符串

# 非流式 _detect_and_parse_impl
splits = processed_text.split(self.think_end_token, maxsplit=1)   # 第一次字面出现
reasoning_text, normal_text = splits[0], splits[1]

# 流式 _parse_streaming_increment_impl
if self._in_reasoning and self.think_end_token in current_text:
    end_idx = current_text.find(self.think_end_token)
```

而 `</think>` 是 **added token 但 `special=false`**：

- detokenize 之后它就是一个普通字符串 `</think>`，与"模型手写的 3 个文本 token（`</`+`think`+`>`）"
  **在文本层面完全无法区分**；
- `skip_special_tokens=True` **不会**清掉它（所以它能原样出现在正文里）；
- `split_special_tokens=True` **也拆不开**它（因为它 `special=false`）。

于是：只要模型在**思考过程中引用**了这个标记（例如在反引号里讨论 `</think>` 的解析逻辑），
解析器就会在这里切断，把"剩下的思考"整体丢进 `content`。

## 3. 判据（怎么确认你的部署也有这个问题）

对同一次响应同时拿到 `reasoning_content`/`content` 与 `logprobs`（token 列表），然后：

1. 把 token 依次拼成整串 `joined`，找到 `reasoning_content` 是它的**前缀**的位置 → 得到边界下标 `k`；
2. 看 `tokens[k]`：
   - 若是**单个** token 且文本正好等于 `</think>` → 边界是 special token（正常）；
   - 若是 `</` + `think` + `>` 之类的**多 token** → 边界是"文本形态"（有问题的路径）；
3. 统计整个 token 流里 `</think>` 的所有出现处及其形态，观察是否存在
   **"第一次出现是文本形态、后面还有一次单 special token"** —— 这就是"引用被误判"的指纹。

`tools/repro_judge.py` 实现了 1-3 的自动化判定（`boundary` / `boundary_token` / `marker_consumed` / `why_not`）。

**决定性旁证**：同一 prompt 走 `/generate`（原生接口）会直接返回 `output_ids`，可以确认
"模型确实是以 1 个 token 收尾"还是"以 3 个 token 写出这个字符串"。

## 4. 实测结果（6 条真实 case，仅列统计）

| 指标 | 结果 |
|---|---|
| 真边界是**单个 special token** | 6 / 6 |
| 其中被更早的"引用"截胡（文本形态） | 4 / 6 |
| 受害样本的 `content` 长度 | 123 / 1,797 / 17,029 / 32,563 字 |
| 按 special token 重切后 `content` 长度 | 0–725 字 |

也就是说：**问题不是"断句难看"，而是"隐藏思考被当成可见回复"**。

## 5. 修法建议

目标：**只有 token 身份是"结束标记"时才切分**，字符串匹配仅作回退。

三种落地方式（按改动量）：

| 方案 | 做法 | 备注 |
|---|---|---|
| 哨兵替换（最小） | 交文本给解析器前，把"id 属于结束标记"的那个 token 的文本替换成专用哨兵，解析器的 `think_end_token` 设为该哨兵 | 只需几行；解析器逻辑不动 |
| 传字符偏移 | 把"真标记的字符区间"传给解析器，只在该处切 | 约 30–50 行 |
| detokenize 前切 | 直接按 `output_ids` 找第一个结束标记的下标 `i`，`reasoning=detok(ids[:i])`、`content=detok(ids[i+1:])` | 最干净；流式需维护跨 chunk 偏移 |

**必须保留的边界条件**：

1. **回退**：若输出里根本没有 special 结束标记（模型真用文本形态收尾），必须回退到字符串规则，
   否则思考永不闭合，整个回答都会变成 `reasoning_content`。
2. 保留"未写结束标记也能结束思考"的通路（`<tool_call>` 之类的工具起始标记、EOS、角色标记）。
3. 流式里标记会跨 delta 拆开（`</` + `think` + `>`），必须按 **token id** 判定，不能用文本前缀匹配。
4. **客户端也要改**：如果 harness 自己也按字符串切 `</think>`，问题只是搬家；
   而且被错误交付的"伪 content"会作为历史回传，污染后续轮次。

## 6. 相关但独立的两个发现

### 6.1 输入侧：消息正文里的控制符会被 tokenizer 识别成真控制符

在渲染后的整串上，tokenizer 会按 added-token 表**优先最长匹配**，所以 user 消息里写的
`<|user|>` / `</think>` / `<tool_call>` 等字符串，**进入模型输入时就是控制符**，不是普通文本。
实测判据：把同样的 10 个 `</think>` 塞进 user 消息，端点返回的 `usage.prompt_tokens`
等于"特殊标记优先匹配"的计数，而不等于"强制拆成文本"或"被清理"的计数。

⇒ 防御：**网关层中和不可信内容里的控制符字面串**（`</think>` 因 `special=false`，只能替换字符串）。

### 6.2 缓存：生成侧的文本形态回传后会被合并

模型有时以 3 个文本 token 写出 `</think>`；这段文本回传进下一轮 prompt 时会被合并成 **1 个** special token
⇒ history 的 token 序列与生成时不同（retokenization drift）。

它是否造成 KV 缓存损失，取决于**生成出来的 token 是否进入前缀缓存**。判定方法（`tools/probe_cache_reuse_modes.py`）：

1. 让模型生成 N 个 token；
2. 把输出**原样**拼回 prompt 再发一次，看 `cached_tokens`；
3. 若命中到"全序列页对齐"值 → 生成 token 可复用 ⇒ 漂移点之后全部要重算；
   若只命中到"仅 prompt 页对齐"值 → 生成 token 不可复用 ⇒ 漂移无缓存代价。

（在 PD 分离部署里，prefill 实例只算 prompt 的 KV，生成发生在 decode 实例上，
因此 prefill 侧的前缀缓存天然不含生成 token —— 这时漂移没有缓存代价，但"模型回看自己历史时
tokenization 变了"这件事仍然成立。）
