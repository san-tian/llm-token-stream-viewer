#!/usr/bin/env python3
"""
把 think-tag-query-kit 里的 6 个 case 逐个打给端点，非流式抓取「完整请求 + 完整响应」。

每个 case 落盘（cases/<id>/）：
  request.json          实际发出的请求体（curl --data-binary 直接读它）
  trace.txt             curl --trace（hex，含实际收发字节；Authorization 凭据按位遮挡）
  request_headers.txt   请求头（凭据已遮挡）
  headers.txt           响应头
  body.json             实际收到的响应体
  meta.json             计时、http 状态、字段长度、接缝摘要
用法: python3 capture_cases.py [case_id ...]
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
CASES = ROOT / "cases"
KIT = pathlib.Path(os.environ.get("KIT_DIR", "/home/dev/Deployment/think-tag-query-kit-20261015"))
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:30000/v1/chat/completions")
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
KEY = os.environ["PROBE_KEY"]
MAX_TOKENS = int(os.environ.get("MAXTOK", "32768"))   # 本次请求的 max_tokens（我们自己设的）；
                                                      # 服务端上下文上限 1,048,576（见 /v1/models）
EFFORT = os.environ.get("EFFORT", "low")
TIMEOUT = int(os.environ.get("CASE_TIMEOUT", "900"))
RUN_LABEL = os.environ.get("RUN_LABEL", "run1")
LOGPROBS = os.environ.get("LOGPROBS", "") not in ("", "0")


def payload_for(case):
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": case["system"]}] + case["messages"],
        "tools": [{"type": "function", "function": t} for t in case["tools"]],
        "reasoning_effort": EFFORT,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    if LOGPROBS:
        payload["logprobs"] = True          # 让响应带 logprobs.content：逐 token（reasoning+content 都在里面）
    return payload


def redact_trace(path, key_bytes):
    """把 trace 里 Authorization 的凭据按位改成 2a，保持行布局与字节数不变。"""
    lines = path.read_text().split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("=> Send header"))
    except StopIteration:
        return 0
    end = next((i for i, l in enumerate(lines) if i > start and l[:2] in ("=>", "<=", "==")), len(lines))
    pos, buf = [], bytearray()
    for li in range(start, end):
        l = lines[li]
        if not re.match(r"^[0-9a-f]{4}: ", l):
            continue
        for i in range(16):
            col = 6 + 3 * i
            if col + 2 > len(l):
                break
            tok = l[col:col + 2]
            if any(ch not in "0123456789abcdef" for ch in tok):
                break
            pos.append((li, col, 54 + i)); buf += bytes.fromhex(tok)
    idx = buf.find(key_bytes)
    if idx < 0:
        return 0
    for n in range(idx, idx + len(key_bytes)):
        li, col, acol = pos[n]
        l = lines[li]
        lines[li] = l[:col] + "2a" + l[col + 2:acol] + "*" + l[acol + 1:]
    path.write_text("\n".join(lines))
    return len(key_bytes)


def req_headers_from_trace(path, marker="Authorization"):
    txt = path.read_text()
    seg = txt.split("=> Send header", 1)[1].split("\n=>", 1)[0]
    hx = "".join(m.group(1).replace(" ", "") for m in
                 re.finditer(r"^[0-9a-f]{4}: ((?:[0-9a-f]{2} )+)", seg, re.M))
    out = []
    for ln in bytes.fromhex(hx).decode("utf-8", "replace").split("\r\n"):
        if ln.lower().startswith(marker.lower()):
            v = ln.split(":", 1)[1].strip()
            out.append(f"Authorization: <已遮挡 {len(v)} 字节>   ← 凭据按位替换为 '*'（trace 同步遮挡）")
        else:
            out.append(ln)
    return "\r\n".join(out)


def summarize(body_path):
    raw = body_path.read_bytes()
    try:
        d = json.loads(raw)
    except ValueError:
        return {"parse": "failed", "bytes": len(raw)}
    ch = (d.get("choices") or [{}])[0]
    m = ch.get("message") or {}
    R = m.get("reasoning_content") or ""
    C = m.get("content") or ""
    tc = m.get("tool_calls") or []
    seam = None
    if R and C:
        seam = {"reasoning_tail": R[-160:], "content_head": C[:160],
                "concat": R[-70:] + C[:70]}
    return {
        "parse": "ok",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "finish_reason": ch.get("finish_reason"),
        "usage": d.get("usage"),
        "reasoning_chars": len(R), "content_chars": len(C),
        "markers": {"reasoning": {"<think>": R.count("<think>"), "</think>": R.count("</think>")},
                    "content": {"<think>": C.count("<think>"), "</think>": C.count("</think>")}},
        "tool_calls": [{"name": (c.get("function") or {}).get("name"),
                        "args_chars": len((c.get("function") or {}).get("arguments") or "")} for c in tc],
        "seam": seam,
    }


def main():
    index = json.loads((KIT / "queries" / "index.json").read_text())
    want = sys.argv[1:] or [c["case_id"] for c in index["cases"]]
    byid = {c["case_id"]: c for c in index["cases"]}
    CASES.mkdir(exist_ok=True)
    for cid in want:
        entry = byid[cid]
        case = json.loads((KIT / entry["file"]).read_text())
        out = CASES / cid / RUN_LABEL
        out.mkdir(parents=True, exist_ok=True)
        (out / "request.json").write_text(
            json.dumps(payload_for(case), ensure_ascii=False, indent=2), encoding="utf-8")
        nreq = len(case["messages"]) + 1
        print(f"[{cid}/{RUN_LABEL}] 请求体 {(out/'request.json').stat().st_size} 字节 "
              f"({nreq} 条消息, {len(case['tools'])} 个工具) → 发送 …", flush=True)
        t0 = time.time()
        r = subprocess.run(
            ["curl", "-sS", "--trace", str(out / "trace.txt"), "-D", str(out / "headers.txt"),
             "-o", str(out / "body.json"), BASE,
             "-H", f"Authorization: Bearer {KEY}", "-H", "Content-Type: application/json",
             "--data-binary", f"@{out/'request.json'}", "--max-time", str(TIMEOUT)],
            capture_output=True, text=True)
        el = time.time() - t0
        (out / "request_headers.txt").write_text(req_headers_from_trace(out / "trace.txt"))
        nred = redact_trace(out / "trace.txt", KEY.encode())
        meta = {"case_id": cid, "run": RUN_LABEL, "name": entry["name"], "kind": entry["kind"],
                "observation": entry["observation"],
                "endpoint": BASE, "model": MODEL, "stream": False,
                "max_tokens": MAX_TOKENS, "reasoning_effort": EFFORT,
                "elapsed_seconds": round(el, 1), "curl_exit": r.returncode,
                "curl_stderr": (r.stderr or "")[:400], "redacted_key_bytes": nred,
                "request_bytes": (out / "request.json").stat().st_size}
        if (out / "body.json").exists():
            meta.update(summarize(out / "body.json"))
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        seam = "有接缝" if meta.get("seam") else "无接缝"
        print(f"[{cid}/{RUN_LABEL}] {el:.0f}s exit={r.returncode} finish={meta.get('finish_reason')} "
              f"reasoning={meta.get('reasoning_chars')} content={meta.get('content_chars')} {seam}", flush=True)
    print("ALL DONE")


if __name__ == "__main__":
    main()
