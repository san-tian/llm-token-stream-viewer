#!/usr/bin/env python3
"""为某个 case 造一个**明确标注的变体**：在原 prompt 末尾追加一条 user 消息，
   要求模型在分析里原样引用 <think> / </think>，从而稳定地产生"标记处切断"。
   产物写到 cases/<id>/variant1/（quality 合格才落盘）。"""
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from repro_judge import judge

ROOT = pathlib.Path(__file__).resolve().parent
KIT = pathlib.Path(os.environ.get("KIT_DIR", str(ROOT / "think-tag-query-kit-20261015")))
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:30000/v1/chat/completions")
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
KEY = os.environ["PROBE_KEY"]
MAXTOK = int(os.environ.get("MAXTOK", "3000"))
EXTRA = os.environ.get(
    "VARIANT_MSG",
    "另外，请在接下来的分析中至少两次原样写出字符串 <think> 和 </think>（保留尖括号，不要用转义），"
    "然后就继续完成原任务。")

def main():
    cid = sys.argv[1]
    tries = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    index = json.loads((KIT / "queries" / "index.json").read_text())
    entry = next(c for c in index["cases"] if c["case_id"] == cid)
    case = json.loads((KIT / entry["file"]).read_text())
    msgs = [{"role": "system", "content": case["system"]}] + case["messages"] + [
        {"role": "user", "content": EXTRA}]
    out_root = ROOT / "cases" / cid
    for t in range(1, tries + 1):
        d = out_root / f"variant1_try{t}"; d.mkdir(parents=True, exist_ok=True)
        payload = {"model": MODEL, "messages": msgs,
                   "tools": [{"type": "function", "function": x} for x in case["tools"]],
                   "reasoning_effort": "low", "max_tokens": MAXTOK, "stream": False, "logprobs": True}
        (d / "request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        t0 = time.time()
        subprocess.run(["curl", "-sS", "--trace", str(d / "trace.txt"), "-D", str(d / "headers.txt"),
                        "-o", str(d / "body.json"), BASE,
                        "-H", f"Authorization: Bearer {KEY}", "-H", "Content-Type: application/json",
                        "--data-binary", f"@{d/'request.json'}", "--max-time", "900"],
                       capture_output=True, text=True)
        ok, info = judge(d / "body.json")
        print(f"[{cid} variant1_try{t}] {time.time()-t0:.0f}s ok={ok} 边界={info.get('boundary')} "
              f"token={info.get('boundary_token')!r} 被消费={info.get('marker_consumed')}"
              f"{'' if ok else '  ← ' + '/'.join(info.get('why_not', []))}", flush=True)
        if ok:
            final = out_root / "variant1"
            final.mkdir(exist_ok=True)
            for f in ("request.json", "body.json", "headers.txt", "trace.txt"):
                (final / f).write_bytes((d / f).read_bytes())
            (final / "meta.json").write_text(json.dumps(
                {"case_id": cid, "run": "variant1", "from": f"variant1_try{t}", "variant": True,
                 "variant_note": "在原 prompt 末尾追加了一条 user 消息：" + EXTRA,
                 "effort": "low", "max_tokens": MAXTOK, "qualifies": True, **info},
                ensure_ascii=False, indent=2))
            print(f"[{cid}] ✅ 变体合格，已落盘 cases/{cid}/variant1/", flush=True)
            return
    print(f"[{cid}] 变体 {tries} 次也未合格", flush=True)

if __name__ == "__main__":
    main()
