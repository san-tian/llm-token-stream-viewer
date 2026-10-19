#!/usr/bin/env python3
"""
抓取「原始 token 流」——用来判断某个 `</think>` 到底是 special token 还是普通文本。

端点能力（本机实测）：
  POST /generate            sglang 原生接口，返回 output_ids（token id 列表）
  + return_logprob=true + return_text_in_logprobs=true
      → meta_info.output_token_logprobs = [[logprob, token_id, "token 文本"], ...]
  /v1/chat/completions      不返回 token id；给出 reasoning_content / content 的切分
  /v1/tokenize              文本 → id（用来确认哪些“字符串”其实是单个特殊 token）

每个 prompt 抓三份：
  gen_speckeep.json   /generate，skip_special_tokens=false（特殊标记会出现在 text 里）
  gen_skipdrop.json   /generate，skip_special_tokens=true （特殊标记被丢弃 —— 对照）
  chat.json           /v1/chat/completions 非流式（推理/正文切分）
用法: PROBE_KEY=... python3 capture_tokens.py [slug ...]
"""
import json
import os
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "tokens"
BASE = os.environ.get("TOKEN_BASE", os.environ.get("TOKEN_BASE", "http://127.0.0.1:30000"))
KEY = os.environ["PROBE_KEY"]
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
MAX_NEW = int(os.environ.get("MAX_NEW", "4096"))   # /generate 的新增 token 上限（自己设的）

# 已知的特殊 token（由 /v1/tokenize 逐个实测得到；见 analysis/special_tokens_glm52.json）
SPECIAL = {
    154820: "<|endoftext|>", 154822: "[gMASK]", 154824: "<sop>", 154825: "<eop>",
    154826: "<|system|>", 154827: "<|user|>", 154828: "<|assistant|>", 154829: "<|observation|>",
    154830: "<|begin_of_image|>", 154831: "<|end_of_image|>", 154841: "<think>", 154842: "</think>",
    154843: "<tool_call>", 154844: "</tool_call>", 154854: "<|image|>", 154855: "<|video|>",
}

PROMPTS = {
    "baseline_ok": "只回答 OK",
    "quote_close": "请原样写出字符串 </think> 一次（保留尖括号），然后只回答 DONE。",
    "quote_both": "在分析中请原样写出字符串 <think> 和 </think>（保留尖括号，不要用转义），至少各写两次；最后一行只回答 DONE。",
    "kit_escaped": None,           # ← 与 cases/minimal_template_escaped 同一个 prompt
    "artifact_python_target": None,  # ← 与 cases/artifact_python_target 同一个 prompt
}

KIT = ROOT / "think-tag-query-kit-20261015"
for _slug, _f in [("kit_escaped", "minimal_template_escaped"),
                  ("artifact_python_target", "artifact_python_target")]:
    try:
        _q = json.loads((KIT / "queries" / f"{_f}.json").read_text())
        PROMPTS[_slug] = _q["messages"][0]["content"]
    except Exception:
        PROMPTS.pop(_slug, None)


def post(path, payload, timeout=900):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), round(time.time() - t0, 1)
    except Exception as e:  # noqa: BLE001
        body = ""
        if hasattr(e, "read"):
            try: body = e.read().decode()[:400]
            except Exception: pass
        return {"__error__": f"{type(e).__name__}: {e}", "__body__": body}, round(time.time() - t0, 1)


def gen(text_prompt, skip_special, start_len=0):
    """temperature=0 → 两次调用（skip true/false）是同一段生成，对照才有效；
       logprob_start_len=0 → meta_info.input_token_logprobs 里给出**输入**的 token id。"""
    return post("/generate", {
        "text": text_prompt,
        "sampling_params": {"max_new_tokens": MAX_NEW, "temperature": 0.0,
                            "skip_special_tokens": bool(skip_special)},
        "return_logprob": True,
        "return_text_in_logprobs": True,
        "logprob_start_len": start_len,
    })


def wrap(prompt):
    return f"<|user|>\n{prompt}<|assistant|>\n"


def summarize(gen_res):
    if "__error__" in gen_res:
        return {"error": gen_res["__error__"][:200]}
    mi = gen_res.get("meta_info") or {}
    toks = [(t[1], t[2]) for t in (mi.get("output_token_logprobs") or []) if len(t) >= 3]
    ids = gen_res.get("output_ids") or [t[0] for t in toks]
    spec_hits = [{"i": i, "id": tid, "text": txt} for i, (tid, txt) in enumerate(toks) if tid in SPECIAL]
    text = gen_res.get("text") or ""
    joined = "".join((t[1] or "") for t in toks)
    import difflib
    drops = []
    if joined != text:
        sm = difflib.SequenceMatcher(None, joined, text, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace") and i2 > i1:
                drops.append({"kind": tag, "token_text": joined[i1:i2],
                              "ids": [t[0] for t in toks]})
    return {
        "n_tokens": len(ids),
        "input_ids": [t[1] for t in (mi.get("input_token_logprobs") or []) if len(t) >= 3],
        "joined_equals_text": joined == text,
        "dropped_from_text": drops,
        "ids": ids,
        "tokens": [{"id": t[0], "text": t[1], "special": SPECIAL.get(t[0])} for t in toks],
        "special_hits": spec_hits,
        "text_has_open": "<think>" in text,
        "text_has_close": "</think>" in text,
        "finish": (mi.get("finish_reason") or {}),
        "reasoning_tokens": mi.get("reasoning_tokens"),
        "spec_accept_rate": mi.get("spec_accept_rate"),
        "text": text,
    }


def main():
    import sys
    want = sys.argv[1:] or list(PROMPTS)
    OUT.mkdir(exist_ok=True)
    for slug in want:
        prompt = PROMPTS[slug]
        d = OUT / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt.txt").write_text(prompt)
        print(f"[{slug}] /generate (skip_special_tokens=false) …", flush=True)
        keep, el1 = gen(wrap(prompt), False)
        (d / "gen_speckeep.json").write_text(json.dumps(keep, ensure_ascii=False, indent=2))
        print(f"[{slug}] /generate (skip_special_tokens=true) …", flush=True)
        drop, el2 = gen(wrap(prompt), True)
        (d / "gen_skipdrop.json").write_text(json.dumps(drop, ensure_ascii=False, indent=2))
        print(f"[{slug}] /v1/chat/completions …", flush=True)
        chat, el3 = post("/v1/chat/completions", {
            "model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_NEW, "temperature": 1.0, "stream": False,
            "reasoning_effort": "low"})
        (d / "chat.json").write_text(json.dumps(chat, ensure_ascii=False, indent=2))
        meta = {
            "slug": slug, "prompt": prompt,
            "wrapped_prompt": wrap(prompt),
            "endpoint": BASE, "model": MODEL, "max_new_tokens": MAX_NEW,
            "elapsed": {"gen_keep": el1, "gen_drop": el2, "chat": el3},
            "special_token_map": SPECIAL,
            "gen_keep": summarize(keep),
            "gen_drop": summarize(drop),
        }
        if "choices" in chat:
            m = (chat["choices"][0].get("message") or {})
            R, C = (m.get("reasoning_content") or ""), (m.get("content") or "")
            meta["chat"] = {"finish": chat["choices"][0].get("finish_reason"),
                            "usage": chat.get("usage"),
                            "reasoning_chars": len(R), "content_chars": len(C),
                            "reasoning_has_marker": ("<think>" in R) or ("</think>" in R),
                            "content_has_marker": ("<think>" in C) or ("</think>" in C),
                            "reasoning_tail": R[-160:], "content_head": C[:160]}
        else:
            meta["chat"] = {"error": str(chat)[:200]}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        gk, gd = meta["gen_keep"], meta["gen_drop"]
        print(f"[{slug}] tokens={gk.get('n_tokens')} 特殊标记命中={len(gk.get('special_hits') or [])} "
              f"| text 含 </think> (keep/drop)={gk.get('text_has_close')}/{gd.get('text_has_close')} "
              f"| chat reasoning/content={meta['chat'].get('reasoning_chars')}/{meta['chat'].get('content_chars')}",
              flush=True)
    print("DONE")


if __name__ == "__main__":
    main()
