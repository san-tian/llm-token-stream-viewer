#!/usr/bin/env python3
"""对齐实验：给 /generate 喂 chat 端点实际使用的模板（Qwen 风格），temperature=0，
   使 /generate 的 token 流与 /v1/chat/completions 的 reasoning/content 切分可对齐。"""
import json, os, pathlib, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "tokens" / "_align"
BASE = os.environ.get("TOKEN_BASE", os.environ.get("TOKEN_BASE", "http://127.0.0.1:30000"))
KEY = os.environ["PROBE_KEY"]
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")

SYS = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
PROMPTS = {
    "align_ok": "只回答 OK",
    "align_close": "请原样写出字符串 </think> 一次（保留尖括号），然后只回答 DONE。",
    "align_both": "在分析中请原样写出字符串 <think> 和 </think>（保留尖括号，不要用转义），至少各写两次；最后一行只回答 DONE。",
}

def post(path, payload, timeout=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), round(time.time() - t0, 1)
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            try: body = e.read().decode()[:300]
            except Exception: pass
        return {"__error__": f"{type(e).__name__}: {e}", "__body__": body}, round(time.time() - t0, 1)

def qwen_prompt(user):
    return (f"<|im_start|>system\n{SYS}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n")

OUT.mkdir(parents=True, exist_ok=True)
for slug, user in PROMPTS.items():
    d = OUT / slug; d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.txt").write_text(user)
    wrapped = qwen_prompt(user)
    gen, e1 = post("/generate", {"text": wrapped,
        "sampling_params": {"max_new_tokens": 400, "temperature": 0.0, "skip_special_tokens": False},
        "return_logprob": True, "return_text_in_logprobs": True, "logprob_start_len": 0})
    (d / "gen.json").write_text(json.dumps(gen, ensure_ascii=False, indent=2))
    chat, e2 = post("/v1/chat/completions", {"model": MODEL,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": 400, "temperature": 0.0, "stream": False, "reasoning_effort": "low"})
    (d / "chat.json").write_text(json.dumps(chat, ensure_ascii=False, indent=2))
    mi = gen.get("meta_info") or {}
    toks = [(t[1], t[2]) for t in (mi.get("output_token_logprobs") or []) if len(t) >= 3]
    m = ((chat.get("choices") or [{}])[0].get("message") or {})
    R, C = m.get("reasoning_content") or "", m.get("content") or ""
    print(f"[{slug}] gen={len(toks)} tok ({e1}s) | chat r/c={len(R)}/{len(C)} ({e2}s) | "
          f"gen text={json.dumps((gen.get('text') or '')[:60], ensure_ascii=False)}")
print("DONE")
