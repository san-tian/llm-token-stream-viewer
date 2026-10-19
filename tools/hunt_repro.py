#!/usr/bin/env python3
"""
为每个 case 猎取一条「复现了字段切断 **且** 带 token 序列（logprobs）」的运行。

判定（都在同一次响应里可得，可复核）：
  · 边界定位：把 logprobs 的 token 依次拼接，前 k 个正好等于 reasoning_content
  · 标记在边界：第 k 个 token 起拼出的文本 == "</think>"（单 token 特殊 token 或 3 个文本 token）
  · 标记被消费："</think>" 出现在「全部 token 拼接」与「reasoning+content」的差集里
  · 可见切断：reasoning 去空白后不以句末标点结尾，或引号/反引号未闭合

产出：cases/<id>/try<N>/（每次尝试）与 cases/<id>/repro/（第一条判定合格的运行）
用法: PROBE_KEY=... python3 hunt_repro.py [case_id ...]
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
CASES = ROOT / "cases"
KIT = pathlib.Path(os.environ.get("KIT_DIR", str(ROOT / "think-tag-query-kit-20261015")))
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:30000/v1/chat/completions")
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
KEY = os.environ["PROBE_KEY"]
MAXTOK = int(os.environ.get("MAXTOK", "32768"))   # 复现运行给足预算，避免"末尾像没说完"被误读
                                                 # 服务端上下文上限 1,048,576
ATTEMPTS = int(os.environ.get("ATTEMPTS", "6"))
TIMEOUT = int(os.environ.get("CASE_TIMEOUT", "900"))
REQUIRE_NATURAL = os.environ.get("REQUIRE_NATURAL", "") not in ("", "0")   # 只接受未被 max_tokens 截断的运行


def payload_for(case, effort, temperature):
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content": case["system"]}] + case["messages"],
        "tools": [{"type": "function", "function": t} for t in case["tools"]],
        "reasoning_effort": effort,
        "temperature": temperature,
        "max_tokens": MAXTOK,
        "stream": False,
        "logprobs": True,
    }


from repro_judge import judge  # noqa: E402


def main():
    index = json.loads((KIT / "queries" / "index.json").read_text())
    byid = {c["case_id"]: c for c in index["cases"]}
    want = sys.argv[1:] or list(byid)
    for cid in want:
        case = json.loads((KIT / byid[cid]["file"]).read_text())
        out = CASES / cid
        out.mkdir(parents=True, exist_ok=True)
        got = False
        for attempt in range(1, ATTEMPTS + 1):
            effort = ["medium", "high", "low"][(attempt - 1) % 3]
            temperature = [1.0, 1.2][(attempt - 1) % 2]
            d = out / f"try{attempt}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "request.json").write_text(json.dumps(payload_for(case, effort, temperature), ensure_ascii=False, indent=2))
            t0 = time.time()
            subprocess.run(["curl", "-sS", "--trace", str(d / "trace.txt"), "-D", str(d / "headers.txt"),
                            "-o", str(d / "body.json"), BASE,
                            "-H", f"Authorization: Bearer {KEY}", "-H", "Content-Type: application/json",
                            "--data-binary", f"@{d/'request.json'}", "--max-time", str(TIMEOUT)],
                           capture_output=True, text=True)
            el = round(time.time() - t0, 1)
            ok, info = judge(d / "body.json")
            if ok and REQUIRE_NATURAL and info.get("finish") == "length":
                ok = False
                info["why_not"] = ["被 max_tokens 截断（要求自然结束）"] + info.get("why_not", [])
            (d / "meta.json").write_text(json.dumps(
                {"case_id": cid, "run": f"try{attempt}", "attempt": attempt, "effort": effort,
                 "temperature": temperature,
                 "max_tokens": MAXTOK, "elapsed_seconds": el, "qualifies": ok, **info},
                ensure_ascii=False, indent=2))
            print(f"[{cid} try{attempt}] {el}s ok={ok} 边界={info.get('boundary')} "
                  f"token={info.get('boundary_token')!r} 被消费={info.get('marker_consumed')} "
                  f"可见切断={info.get('not_sentence_end') or info.get('unbalanced_backtick') or info.get('unbalanced_dquote')}"
                  f"{'' if ok else '  ← ' + '/'.join(info.get('why_not', []))}", flush=True)
            if ok:
                # 复制成 repro
                rd = out / "repro"
                rd.mkdir(exist_ok=True)
                for f in ("request.json", "body.json", "headers.txt", "trace.txt", "meta.json"):
                    if (d / f).exists():
                        (rd / f).write_bytes((d / f).read_bytes())
                (rd / "meta.json").write_text(json.dumps(
                    {"case_id": cid, "run": "repro", "from": f"try{attempt}", "effort": effort,
                     "temperature": temperature,
                     "max_tokens": MAXTOK, "elapsed_seconds": el, "qualifies": True, **info},
                    ensure_ascii=False, indent=2))
                got = True
                break
        if not got:
            print(f"[{cid}] {ATTEMPTS} 次尝试都没出现合格的切断（仍是同一机制：标记被消费，只是边界处文本读起来完整）", flush=True)
    print("HUNT DONE")


if __name__ == "__main__":
    main()
