#!/usr/bin/env python3
"""生成三个合成 demo case（不含任何真实数据）。
 01 单 special token 收尾（无损）
 02 引用里的 </think>（3 个文本 token）被误判为结束符 → 思考被截短、其余漏进 content
 03 正文里原样交付一个 special 形态的 </think>
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "cases"
CLOSE = "</think>"

def toks(s, size=4):
    return [s[i:i + size] for i in range(0, len(s), size)]

def build(cid, reasoning, content, *, boundary_mode, extra_tokens_after_content=(), finish="stop"):
    """boundary_mode: 'special' | 'text_in_quote' | 'residue_in_content'"""
    if boundary_mode == "special":
        stream = toks(reasoning) + [CLOSE] + toks(content) + list(extra_tokens_after_content)
        k = len(toks(reasoning))
        delivered_reasoning, delivered_content = reasoning, content
        boundary_tokens = [CLOSE]
    elif boundary_mode == "text_in_quote":
        # 思考里引用了 `</think>`（3 个文本 token）→ 解析器在这里就切了
        quote_at = reasoning.index("`" + CLOSE + "`")
        head, tail = reasoning[:quote_at + 1], reasoning[quote_at + 1 + len(CLOSE):]
        stream = toks(head) + ["</", "think", ">"] + toks(tail) + toks(content)
        k = len(toks(head))
        delivered_reasoning, delivered_content = head, tail + content
        boundary_tokens = ["</", "think", ">"]
    else:  # residue_in_content: 正文里出现 special 形态
        stream = toks(reasoning) + [CLOSE] + toks(content[:-len(CLOSE)]) + [CLOSE]
        k = len(toks(reasoning))
        delivered_reasoning, delivered_content = reasoning, content
        boundary_tokens = [CLOSE]
    lp = [{"token": t, "logprob": -0.05} for t in stream]
    body = {
        "id": f"demo-{cid}", "object": "chat.completion", "model": "your-model",
        "choices": [{
            "index": 0, "finish_reason": finish, "matched_stop": None,
            "message": {"role": "assistant", "reasoning_content": delivered_reasoning,
                        "content": delivered_content},
            "logprobs": {"content": lp},
        }],
        "usage": {"prompt_tokens": 128, "completion_tokens": len(stream),
                  "reasoning_tokens": k, "total_tokens": 128 + len(stream),
                  "prompt_tokens_details": None},
    }
    meta = {"case_id": cid, "run": "demo", "temperature": 0, "max_tokens": 600,
            "qualifies": True, "finish": finish, "boundary": k,
            "boundary_token": boundary_tokens[0], "n_tokens": len(stream),
            "reasoning_chars": len(delivered_reasoning), "content_chars": len(delivered_content),
            "demo_note": "synthetic data, no real traffic"}
    d = DEMO / cid / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps(
        {"model": "your-model", "messages": [{"role": "user", "content": f"demo prompt for {cid}"}],
         "max_tokens": 600, "temperature": 0}, ensure_ascii=False, indent=1))
    (d / "body.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"  {cid}: {len(stream)} tokens, boundary k={k}, 边界={boundary_tokens}")

build("01_special_token_boundary",
      "The template prefills the opening marker, so the model starts inside a thinking block. "
      "Closing it is a single control token, which the server consumes. ",
      "The opening marker is part of the prompt; the closing one is generated as one control token.",
      boundary_mode="special")
build("02_text_form_in_quote",
      "Let me look at how the parser splits. It searches for the literal string `" + CLOSE + "` in the "
      "detokenised text, and takes the first occurrence. That is the bug: a quoted mention looks identical. ",
      "So the fix is to split on the token id instead of on the string.",
      boundary_mode="text_in_quote")
build("03_marker_residue_in_content",
      "Short reasoning here. ",
      "Answer body. Note that this control token is delivered as text: " + CLOSE,
      boundary_mode="residue_in_content")
