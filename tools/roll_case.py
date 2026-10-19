#!/usr/bin/env python3
"""批量 roll 一个 case 并统计症状。
用法: PROBE_KEY=... python3 roll_case.py <case_id> <N> [--temp 1.2] [--maxtok 3000] [--effort low] [--jobs 3]
症状判据（全部有 logprobs 支撑）：
  residue_in_content : 交付 content 里出现**单 token special 形态**的 </think>（kit 对 0220 的声称）
  residue_any        : content/reasoning 里出现任何协议标记的字面串
  boundary_form      : 边界被消费的标记是「单 special token」还是「文本形态(多 token)」
  midword_tail       : reasoning 结尾停在词中间（非句末标点、非空白）
  unbalanced_bt/dq   : reasoning 里反引号/双引号不闭合
  tail_deg           : 尾部 50 token 平均 logprob 比整体低 0.8 以上（退化/词沙拉代理指标）
"""
import json, os, pathlib, re, sys, statistics, urllib.request
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent
BASE = os.environ.get("TOKEN_BASE", os.environ.get("TOKEN_BASE", "http://127.0.0.1:30000"))
KEY = os.environ.get("PROBE_KEY", "")
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
SPECIAL = {"</think>", "<think>", "<tool_call>", "</tool_call>", "<|user|>", "<|assistant|>",
           "<|system|>", "<|observation|>", "<|endoftext|>", "[gMASK]", "<sop>"}

def post(payload, t=1800):
    if not KEY: raise SystemExit("需要 PROBE_KEY 环境变量")
    r = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as x: return json.loads(x.read())

def analyze(cid, n, temp, maxtok, effort, i):
    req = json.loads((ROOT / "cases" / cid / "repro" / "request.json").read_text())
    pl = {"model": MODEL, "messages": req["messages"], "tools": req.get("tools"),
          "max_tokens": maxtok, "temperature": temp, "logprobs": True, "stream": False}
    if effort: pl["reasoning_effort"] = effort
    try: d = post(pl)
    except Exception as e: return {"i": i, "err": f"{type(e).__name__}: {e}"}
    outdir = ROOT / "cases" / cid / "rolls"
    outdir.mkdir(parents=True, exist_ok=True)
    tag = f"t{str(temp).replace('.', 'p')}"
    (outdir / f"{tag}_roll{i:02d}.json").write_text(json.dumps({"request": pl, "response": d}, ensure_ascii=False))
    return analyze_resp(d, i)


def analyze_resp(d, i):
    ch = d["choices"][0]; m = ch["message"]; u = d["usage"]
    entries = ((ch.get("logprobs") or {}).get("content")) or []
    T = [e.get("token", "") for e in entries]
    L = [e.get("logprob") for e in entries if e.get("logprob") is not None]
    rc, ct = m.get("reasoning_content") or "", m.get("content") or ""
    # 边界：找 </think> 处，判定形态
    full = "".join(T); spans = []; p = 0
    for t in T: spans.append((p, p + len(t))); p += len(t)
    k = None; acc = ""
    for j, t in enumerate(T):
        acc += t
        if acc == rc: k = j + 1; break
    bform, btok = "none", None
    if k is not None and k < len(T):
        cov = [j for j, (s, e) in enumerate(spans) if s < spans[k][1] and spans[k][0] < e]
        btok = T[k]
        bform = "special" if (len([x for x in cov if x == k]) and T[k] == "</think>") else f"text({len(cov)})"
    # content 里的标记
    def marks(txt):
        out = {"close": txt.count("</think>"), "open": txt.count("<think>"),
               "tool": txt.count("<tool_call>"), "role": sum(txt.count(x) for x in ("<|user|>", "<|assistant|>", "<|system|>", "<|observation|>"))}
        return out
    mc, mr = marks(ct), marks(rc)
    # 单 token special 形态出现在哪个字段
    spec_in_content = []
    if k is not None:
        for j in range(k + 1, len(T)):
            if T[j] in SPECIAL: spec_in_content.append(T[j])
    tail = rc.rstrip()
    res = {"i": i, "finish": ch.get("finish_reason"), "ctok": u["completion_tokens"], "rtok": u.get("reasoning_tokens"),
           "rc": len(rc), "ct": len(ct), "bform": bform, "btok": (btok or "")[:12],
           "close_ct": mc["close"], "open_ct": mc["open"], "close_rc": mr["close"], "open_rc": mr["open"],
           "tool_ct": mc["tool"], "role_ct": mc["role"], "tool_rc": mr["tool"], "role_rc": mr["role"],
           "spec_in_content": spec_in_content[:6], "n_spec_content": len(spec_in_content),
           "midword": bool(tail) and (tail[-1] not in "。！？.!?;；:：\n \"')】》」”）`"),
           "unbal_bt": rc.count("`") % 2 == 1, "unbal_dq": rc.count('"') % 2 == 1,
           "mean_lp": round(statistics.mean(L), 3) if L else None,
           "tail_lp": round(statistics.mean(L[-50:]), 3) if len(L) >= 50 else None,
           "ct_head": ct[:60], "rc_tail": rc[-60:], "dropped_n": len(m.get("tool_calls") or [])}
    res["tail_deg"] = (res["mean_lp"] is not None and res["tail_lp"] is not None and res["mean_lp"] - res["tail_lp"] > 0.8)
    return res

def main():
    cid = sys.argv[1]; N = int(sys.argv[2])
    def opt(f, d): return type(d)(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d
    temp = opt("--temp", 1.2); maxtok = opt("--maxtok", 3000); effort = opt("--effort", "low")
    jobs = opt("--jobs", 3)
    tag = f"t{str(temp).replace('.', 'p')}"
    if "--rebuild" in sys.argv:
        d = ROOT / "cases" / cid / "rolls"; groups = {}
        for f in sorted(d.glob("*_roll*.json")):
            tg = f.name.split("_roll")[0]; idx = int(re.findall(r"_roll(\d+)", f.name)[0])
            groups.setdefault(tg, []).append(analyze_resp(json.loads(f.read_text())["response"], idx))
        for tg, rows in groups.items():
            agg = {"n": 0, "residue_in_content": 0, "special_in_content": 0, "midword": 0, "text_boundary": 0,
                   "special_boundary": 0, "empty_ct": 0, "degen": 0, "any_role_in_content": 0}
            for r in rows:
                if "err" in r or "finish" not in r: continue
                agg["n"] += 1
                agg["special_in_content"] += 1 if r["n_spec_content"] else 0
                agg["residue_in_content"] += 1 if (r["close_ct"] or r["open_ct"] or r["tool_ct"]) else 0
                agg["any_role_in_content"] += 1 if r["role_ct"] else 0
                agg["midword"] += 1 if r["midword"] else 0
                agg["text_boundary"] += 1 if r["bform"].startswith("text") else 0
                agg["special_boundary"] += 1 if r["bform"] == "special" else 0
                agg["empty_ct"] += 1 if r["ct"] == 0 else 0
                agg["degen"] += 1 if r["tail_deg"] else 0
            (d / f"summary-{tg}.json").write_text(json.dumps({"case_id": cid, "tag": tg, "agg": agg, "rows": rows},
                                                             ensure_ascii=False, indent=1))
            print(f"  {tg}: n={agg['n']} | " + " ".join(f"{k}={v}" for k, v in agg.items() if k != "n" and v))
        return
    print(f"roll {cid} × {N}（temp={temp} maxtok={maxtok} effort={effort} jobs={jobs}）\n")
    rows = [None] * N
    with ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(analyze, cid, N, temp, maxtok, effort, i): i for i in range(N)}
        for fu in as_completed(futs):
            r = fu.result(); rows[r["i"]] = r
            if "err" in r:
                print(f"{r['i']:>3d} 失败 {r['err'][:70]}", flush=True); continue
            print(f"{r['i']:>3d} {str(r['finish']):>11s} {r['ctok']:>5d} {r['rc']:>6d} {r['ct']:>5d} {r['bform']:>9s} "
                  f"{('close=%d open=%d tool=%d role=%d'%(r['close_ct'],r['open_ct'],r['tool_ct'],r['role_ct'])):>26s} "
                  f"{r['n_spec_content']:>10d} {str(r['midword']):>7s} {str(r['unbal_bt']):>3s} "
                  f"{str(r['mean_lp']):>8s} {str(r['tail_lp']):>8s} {str(r['tail_deg']):>4s}", flush=True)
    agg = {"n": 0, "residue_in_content": 0, "special_in_content": 0, "midword": 0, "text_boundary": 0,
           "special_boundary": 0, "empty_ct": 0, "degen": 0, "any_role_in_content": 0}
    for r in rows:
        if not r or "err" in r: continue
        agg["n"] += 1
        agg["special_in_content"] += 1 if r["n_spec_content"] else 0
        agg["residue_in_content"] += 1 if (r["close_ct"] or r["open_ct"] or r["tool_ct"]) else 0
        agg["any_role_in_content"] += 1 if r["role_ct"] else 0
        agg["midword"] += 1 if r["midword"] else 0
        agg["text_boundary"] += 1 if r["bform"].startswith("text") else 0
        agg["special_boundary"] += 1 if r["bform"] == "special" else 0
        agg["empty_ct"] += 1 if r["ct"] == 0 else 0
        agg["degen"] += 1 if r["tail_deg"] else 0
    print(f"\n=== 统计（{agg['n']} 次有效）===")
    for k, v in agg.items():
        if k != "n": print(f"  {k:20s} {v:>3d} / {agg['n']}  ({v/max(agg['n'],1)*100:.0f}%)")
    (ROOT / "cases" / cid / "rolls" / f"summary-{tag}.json").write_text(json.dumps(
        {"case_id": cid, "temp": temp, "maxtok": maxtok, "effort": effort, "agg": agg, "rows": rows},
        ensure_ascii=False, indent=1))

if __name__ == "__main__": main()
