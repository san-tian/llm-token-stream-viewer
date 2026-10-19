#!/usr/bin/env python3
"""
生成 index.html —— 把「case 抓取（chat 端点：请求/响应/头部/接缝）」与
「token 抓取（/generate：输出/输入 token id 网格）」合并到同一个页面、同一个 case 上。

数据来源：
  cases/<case>/run<N>/   完整请求 + 响应 + 头部 + trace
  tokens/<slug>/         /generate 的 token id/文本 + chat 对照 + 判定
两个目录用 slug 关联（minimal_template_escaped ↔ kit_escaped）。
"""
import hashlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PF_ATTR = ROOT / "prefill_attr"


def collect_rolls(cid):
    """额外 roll 的统计（cases/<cid>/rolls/summary-*.json）"""
    out = []
    for f in sorted((ROOT / "cases" / cid / "rolls").glob("summary-*.json")) if (ROOT / "cases" / cid / "rolls").exists() else []:
        try:
            d = json.loads(f.read_text())
            tg = d.get("tag", "")
            out.append({"tag": tg, "temp": tg.replace("t", "").replace("p", "."), "n": d["agg"]["n"], "agg": d["agg"]})
        except Exception:  # noqa: BLE001
            pass
    return out or None


def collect_prefill(cid):
    f = PF_ATTR / f"{cid}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text())
        labels, idx = [], {}
        ids, texts, lab, sp, st = [], [], [], [], []
        for t in d["tokens"]:
            key = t["src"] + "|" + t["detail"]
            if key not in idx:
                idx[key] = len(labels); labels.append(key)
            ids.append(t["id"]); texts.append(t["text"]); lab.append(idx[key]); st.append(t["s"])
            sp.append(2 if t["special_skipable"] else (1 if t["special"] else 0))
        d["tokens"] = {"ids": ids, "texts": texts, "lab": lab, "sp": sp, "st": st, "labels": labels}
        return d
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ prefill_attr/{cid}.json 读取失败: {e}")
        return None
DATA = pathlib.Path(os.environ.get("DATA_DIR", ROOT))
CASES = DATA / "cases"
TOK = DATA / "tokens"
KIT = pathlib.Path(os.environ.get("KIT_DIR", DATA / "kit"))   # 可选；缺省时用 demo 元数据
OUT = pathlib.Path(os.environ.get("OUT_HTML", ROOT / "index.html"))

KIT_SCRIPTS = KIT / "scripts"
if str(KIT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(KIT_SCRIPTS))

SENT_END = ".。!?！？"

try:
    from repro_judge import judge as repro_judge
except Exception:  # noqa: BLE001
    repro_judge = lambda p: (False, {})  # noqa: E731
SPECIAL = {
    154820: "<|endoftext|>", 154822: "[gMASK]", 154824: "<sop>", 154825: "<eop>",
    154826: "<|system|>", 154827: "<|user|>", 154828: "<|assistant|>", 154829: "<|observation|>",
    154830: "<|begin_of_image|>", 154831: "<|end_of_image|>", 154841: "<think>", 154842: "</think>",
    154843: "<tool_call>", 154844: "</tool_call>", 154854: "<|image|>", 154855: "<|video|>",
}
MARKER_IDS = {154841, 154842}
CASE2TOK = {"minimal_template_escaped": "kit_escaped"}


def blob(p, note=""):
    if not p.exists():                      # 某些运行（如 hunt 的 tryN）没有全部文件
        return {"name": p.name, "text": "", "bytes": 0, "sha256": "-", "note": note or "（该运行未抓取此文件）"}
    b = p.read_bytes()
    return {"name": p.name, "text": b.decode("utf-8", "replace"), "bytes": len(b),
            "sha256": hashlib.sha256(b).hexdigest(), "note": note}


def read(p, d=""):
    try:
        return pathlib.Path(p).read_text(encoding="utf-8")
    except OSError:
        return d


def verdict(reasoning, content):
    if not content:
        return {"kind": "no_content", "label": "content 为空（未发生字段切换）",
                "unbalanced_quote": None, "ends_sentence": None, "markers_in_content": 0}
    tail = reasoning.rstrip()
    unbal = {"dq": reasoning.count('"') % 2 == 1, "bt": reasoning.count("`") % 2 == 1}
    ends = bool(tail) and tail[-1] in SENT_END
    mc = content.count("<think>") + content.count("</think>")
    reasons = []
    if unbal["dq"]:
        reasons.append("reasoning 里双引号未闭合（引号跨到 content）")
    if unbal["bt"]:
        reasons.append("reasoning 里反引号未闭合（行内代码跨到 content）")
    if not ends:
        reasons.append(f"边界不在句末（reasoning 以 {tail[-1]!r} 结尾）")
    if mc:
        reasons.append(f"正文残留字面标记 {mc} 个")
    if reasons:
        return {"kind": "seam" if (unbal["dq"] or unbal["bt"] or not ends) else "residue",
                "label": "；".join(reasons), "unbalanced_quote": unbal["dq"] or unbal["bt"],
                "ends_sentence": ends, "markers_in_content": mc}
    return {"kind": "clean", "label": "边界在句末，引号闭合，正文无字面标记",
            "unbalanced_quote": False, "ends_sentence": True, "markers_in_content": 0}


def controlled_checks(cid, msg):
    try:
        if cid == "artifact_python_target":
            from artifact_check import artifact
            for c in msg.get("tool_calls") or []:
                if (c.get("function") or {}).get("name") == "submit_artifact":
                    return {"name": "artifact_check（受限 AST）",
                            "report": artifact(json.loads(c["function"]["arguments"]).get("python_source", ""))}
        if cid == "minimal_template_escaped":
            C = msg.get("content") or ""
            return {"name": "minimal_final_exact_DONE",
                    "report": {"content_strip_equals_DONE": C.strip() == "DONE", "content_tail_60": C[-60:]}}
    except Exception as e:  # noqa: BLE001
        return {"name": "check failed", "report": {"error": f"{type(e).__name__}: {e}"}}
    return None


def flags(reasoning, content):
    return {"reasoning_unclosed_backtick": reasoning.count("`") % 2 == 1 and bool(content),
            "markers_in_content": {"<think>": content.count("<think>"), "</think>": content.count("</think>")},
            "markers_in_reasoning": {"<think>": reasoning.count("<think>"), "</think>": reasoning.count("</think>")}}


SPECIAL_TEXT = {v: k for k, v in SPECIAL.items()}


def parse_logprobs(rj, R, C):
    """从同一次响应的 logprobs 里还原 token 流，并定位字段边界与被消费的 token。"""
    try:
        ch = (rj.get("choices") or [{}])[0]
        entries = ((ch.get("logprobs") or {}).get("content")) or []
        if not entries:
            return None
        toks = [{"text": e.get("token", ""), "logprob": e.get("logprob"),
                 "bytes": e.get("bytes")} for e in entries]
        for t in toks:
            t["special"] = SPECIAL_TEXT.get(t["text"])
            t["marker"] = t["special"] in ("<think>", "</think>") if t["special"] else False
        joined = "".join(t["text"] for t in toks)
        k, acc = None, ""
        for i, t in enumerate(toks):
            acc += t["text"]
            if acc == R:
                k = i + 1
                break
        if k is not None:
            for i, t in enumerate(toks):
                t["field"] = "reasoning" if i < k else ("content" if i > k else "boundary")
        dropped = []
        if joined != R + C:
            import difflib
            sm = difflib.SequenceMatcher(None, joined, R + C, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag in ("delete", "replace") and i2 > i1:
                    dropped.append({"text": joined[i1:i2], "kind": tag})
        return {"tokens": toks, "n": len(toks), "boundary": k,
                "boundary_text": (toks[k]["text"] if k is not None and k < len(toks) else None),
                "joined_equals": joined == R + C, "dropped": dropped,
                "marker_single": [t["text"] for t in toks if t["marker"]],
                "special_n": len([t for t in toks if t["special"]])}
    except Exception:  # noqa: BLE001
        return None


def collect_runs(cid, meta_extra):
    cdir = CASES / cid
    runs = []
    rdirs = [d for d in sorted([d for d in cdir.iterdir() if d.is_dir()]) if (d / "body.json").exists()] if cdir.exists() else []
    qual_dirs = []                     # 先判定哪些运行合格（只内嵌第一条合格的，其余留摘要）
    for rd in rdirs:
        _q, _ = repro_judge(rd / "body.json")
        if _q:
            qual_dirs.append(rd.name)
    def _temp_of(name):
        f = cdir / name / "meta.json"
        return (json.loads(f.read_text()).get("temperature") if f.exists() else None)

    def _faith(name):
        t = _temp_of(name)
        return t is None or t <= 1.05

    all_names = [d.name for d in rdirs]
    # 与展示逻辑一致：忠实温度下合格 → 忠实温度 → 任意合格 → 第一个
    keep_dir = (( [n for n in qual_dirs if _faith(n)] or [n for n in all_names if _faith(n)]
                 or qual_dirs or all_names ) or [None])[0]
    for rd in rdirs:
        m = json.loads((rd / "meta.json").read_text()) if (rd / "meta.json").exists() else {}
        req, res = blob(rd / "request.json"), blob(rd / "body.json")
        reqh, resh = blob(rd / "request_headers.txt"), blob(rd / "headers.txt")
        try:
            rj = json.loads(res["text"])
            ch = (rj.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            R = msg.get("reasoning_content") or ""
            C = msg.get("content") or ""
        except ValueError:
            rj, ch, msg, R, C = {}, {}, {}, "", ""
        _ok, _info = repro_judge(rd / "body.json")
        _usage = (rj.get("usage") or {}) if 'rj' in dir() else {}
        light = (rd.name != keep_dir)   # 只给被展示的那条嵌入完整数据（含 logprobs）
        runs.append({
            "label": m.get("run") or rd.name,
            "primary": rd.name == keep_dir,
            "qualifies": bool(_ok), "reproInfo": _info,
            "variant": bool(m.get("variant")), "variantNote": m.get("variant_note") or "",
            "request": req if not light else {"name": req["name"], "bytes": req["bytes"], "sha256": req["sha256"], "text": "", "stripped": True},
            "response": res if not light else {"name": res["name"], "bytes": res["bytes"], "sha256": res["sha256"], "text": "", "stripped": True},
            "reqHeaders": reqh, "resHeaders": resh,
            "toolCalls": [{"name": (c.get("function") or {}).get("name"),
                           "arguments": (c.get("function") or {}).get("arguments")} for c in (msg.get("tool_calls") or [])],
            "checks": controlled_checks(cid, msg), "verdict": verdict(R, C),
            "logprobs": (parse_logprobs(rj, R, C) if not light else None),
            "meta": {"elapsed": m.get("elapsed_seconds"),
                     "finish": (ch.get("finish_reason") if ch else None) or m.get("finish_reason") or m.get("finish"),
                     "usage": _usage or m.get("usage"),
                     "reasoning_chars": len(R), "content_chars": len(C), "max_tokens": m.get("max_tokens"),
                     "truncated": bool((ch.get("finish_reason") if ch else None) == "length") or bool(_info.get("truncated")),
                     "completion_tokens": (_usage or {}).get("completion_tokens") or (m.get("usage") or {}).get("completion_tokens"),
                     "reasoning_tokens": (_usage or {}).get("reasoning_tokens") or (m.get("usage") or {}).get("reasoning_tokens"),
                     "effort": m.get("reasoning_effort"), "temperature": m.get("temperature"), "flags": flags(R, C),
                     "reasoning_tail": R[-300:], "content_head": C[:300],
                     "concat": (R[-120:] + C[:120]) if (R and C) else ""},
        })
    return runs


CLAIM_CLASS = {}   # 可选：为你的 case 声明「声称的观测」类型（lossy / residue）


def case_evidence(cid, runs, tok, rolls=None):
    """判定这条 case 在我们自己的数据里「看不看得出来」。
    判据：边界被消费的标记是【文本形态】⇒ 吞掉的是正文里真实存在的字符 ⇒ 有损；
          是【special 单 token】⇒ 本来就不属于文本 ⇒ 零损失，只有分界落点。"""
    def bs(r):                                       # 边界 token 是否 = 单个 special </think>
        lp = r.get("logprobs")
        if not lp or lp.get("boundary") is None:
            return None
        k, toks = lp["boundary"], lp["tokens"]
        if k >= len(toks):
            return None
        t = toks[k]
        return bool(t.get("special")) and t.get("text") == "</think>"

    def _faithful(r):
        t = (r.get("meta") or {}).get("temperature")
        return t is None or t <= 1.05

    qual = [r for r in runs if r.get("qualifies")]
    faith = [r for r in runs if _faithful(r)]
    faith_labels = {r["label"] for r in faith}
    f_forms = {r["label"]: bs(r) for r in qual if _faithful(r)}
    lossy_f = sorted(l for l, f in f_forms.items() if f is False)
    lossless_f = sorted(l for l, f in f_forms.items() if f is True)
    # 只有被内嵌（primary）的那条带 logprobs ⇒ 边界形态只对它可判（其余按 meta 参与计数）
    forms = {r["label"]: bs(r) for r in runs if bs(r) is not None}
    lossy = sorted(l for l, f in forms.items() if f is False)
    lossless = sorted(l for l, f in forms.items() if f is True)
    residue = {}
    for r in runs:
        n = len([t for t in (r.get("logprobs") or {}).get("tokens", [])
                 if t.get("field") == "content" and t.get("text") == "</think>" and t.get("special")])
        if n:
            residue[r["label"]] = n
    no_content = bool(qual) and all((r.get("meta") or {}).get("content_chars") == 0 for r in qual)
    residue_f = {k: v for k, v in residue.items() if k in faith_labels}
    claim = CLAIM_CLASS.get(cid)

    def _seen(lossy_set, residue_set):
        return ((claim == "lossy" and bool(lossy_set)) or (claim == "residue" and bool(residue_set))
                or (claim is None and (bool(lossy_set) or bool(residue_set))))

    seen_claim = _seen(lossy_f, residue_f)          # 忠实温度下的证据
    seen_any = _seen(lossy, residue)                # 含高温度变体
    parts = []
    parts.append(f"忠实运行（服务默认温度 ≤1.05）{len(faith)} 次 · 合格 {len(f_forms)} 次"
                 + (f" · 可见切断 {len(lossy_f)} 次" if f_forms else " · 无可见切断"))
    lossy_hot = [l for l in lossy if l not in faith_labels]      # 只在高温度变体里出现的
    if lossy_f:
        parts.append(f"有损切断 ×{len(lossy_f)}（忠实运行：{lossy_f[0]}）")
    if lossy_hot:
        parts.append(f"有损切断 ×{len(lossy_hot)}（仅高温度变体：{lossy_hot[0]}）")
    if lossless:
        hot = "" if all(l in faith_labels for l in lossless) else "，且仅在高温变体中"
        parts.append(f"无损切点 ×{len(lossless)}（边界是 special 单 token{hot}）")
    if residue:
        parts.append("正文里原样交付真 special token `</think>` ×%d（%s）" % (sum(residue.values()), "、".join(residue)))
    if no_content:
        parts.append("复现运行的 content 全为空（未发生字段切换）")
    if not qual:
        parts.append(f"{len(runs)} 次抓取均未复现切断" if runs else "无 chat 抓取")
    if seen_claim:
        cls, tag = "t-ok", "✅ 证据成立"
    elif claim == "residue":
        cls, tag = "t-bad", "❌ 声称的观测未复现"
    elif seen_any:
        cls, tag = "t-warn", "⚠️ 只有痕迹（仅高温度变体）"
    elif qual:
        cls, tag = "t-warn", "⚠️ 只有痕迹（无损）"
    else:
        cls, tag = "t-bad", "❌ 未复现（忠实温度下无可见异常）"
    if rolls:
        for r in rolls:
            a = r["agg"]
            parts.append(f"另 roll {a['n']} 次（temp={r['temp']}）：正文标记载荷 {a['residue_in_content']}/{a['n']}"
                         f" ｜ 词中断 {a['midword']}/{a['n']} ｜ content 空 {a['empty_ct']}/{a['n']}"
                         f" ｜ 尾部退化 {a['degen']}/{a['n']}")
    note = ("kit 自称：" + ("主证据组" if KIT_PRIMARY.get(cid) else "陪衬/候选（primary_suite=false）"))
    return {"tag": tag, "cls": cls, "parts": parts, "claim": claim,
            "claim_seen": bool(seen_claim), "note": note,
            "flags": {"lossy": bool(lossy), "lossless": bool(lossless),
                      "residue": sorted(residue), "no_content": no_content}}


def collect_tokens(slug):
    d = TOK / slug
    if not (d / "meta.json").exists():
        return None
    try:
        meta = json.loads((d / "meta.json").read_text())
        keep = json.loads((d / "gen_speckeep.json").read_text())
        drop = json.loads((d / "gen_skipdrop.json").read_text())
        chat = json.loads((d / "chat.json").read_text())
    except Exception:  # noqa: BLE001
        return None
    mi = keep.get("meta_info") or {}

    def toks(gen):
        out = []
        for t in (gen.get("meta_info") or {}).get("output_token_logprobs") or []:
            if len(t) >= 3:
                out.append({"id": t[1], "text": t[2],
                            "logprob": (round(t[0], 4) if isinstance(t[0], (int, float)) else None)})
        if not out:
            for i in gen.get("output_ids") or []:
                out.append({"id": i, "text": None, "logprob": None})
        for t in out:
            t["special"] = SPECIAL.get(t["id"]); t["marker"] = t["id"] in MARKER_IDS
        return out

    tk = toks(keep)
    input_toks = []
    for t in mi.get("input_token_logprobs") or []:
        if len(t) >= 3:
            input_toks.append({"id": t[1], "text": t[2],
                               "logprob": (round(t[0], 4) if isinstance(t[0], (int, float)) else None),
                               "special": SPECIAL.get(t[1]), "marker": t[1] in MARKER_IDS})
    joined = "".join((t["text"] or "") for t in tk)
    drops = []
    if joined != (keep.get("text") or ""):
        import difflib
        sm = difflib.SequenceMatcher(None, joined, keep.get("text") or "", autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace") and i2 > i1:
                drops.append({"text": joined[i1:i2], "kind": tag})
    ch = (chat.get("choices") or [{}])[0].get("message") or {}
    return {
        "slug": slug, "prompt": meta.get("prompt", ""), "wrapped": meta.get("wrapped_prompt", ""),
        "out": {"tokens": tk, "text": keep.get("text") or "", "n": len(tk),
                "finish": mi.get("finish_reason"), "spec": mi.get("spec_accept_rate"),
                "reasoning_tokens": mi.get("reasoning_tokens")},
        "input": {"tokens": input_toks, "n": len(input_toks)},
        "dropText": drop.get("text") or "", "drops": drops,
        "chat": {"reasoning": ch.get("reasoning_content") or "", "content": ch.get("content") or "",
                 "finish": (chat.get("choices") or [{}])[0].get("finish_reason"), "usage": chat.get("usage")},
        "verdict": {
            "special_close": len([t for t in tk if t["id"] == 154842]),
            "special_open": len([t for t in tk if t["id"] == 154841]),
            "literal_close": (keep.get("text") or "").count("</think>"),
            "literal_open": (keep.get("text") or "").count("<think>"),
        },
    }


KIT_PRIMARY = {}


def main():
    try:                                   # kit 可选：没有就进 demo 模式
        index = json.loads((KIT / "queries" / "index.json").read_text())
    except Exception:
        index = {"cases": []}
    KIT_PRIMARY.clear()
    KIT_PRIMARY.update({c["case_id"]: bool(c.get("primary_suite")) for c in index["cases"]})
    try:
        expected = {c["case_id"]: c for c in
                    json.loads((KIT / "verification" / "expected_cases.json").read_text())["cases"]}
    except Exception:  # noqa: BLE001
        expected = {}

    items = []
    used_tok = set()
    # ① kit 的 6 个 case
    _entries = index["cases"] or (
        [{"case_id": d.name, "name": d.name, "kind": "demo", "observation": "", "primary_suite": False}
         for d in sorted(CASES.iterdir()) if d.is_dir()] if CASES.exists() else [])
    for entry in _entries:
        cid = entry["case_id"]
        runs = collect_runs(cid, {})
        tok_slug = CASE2TOK.get(cid, cid)
        tok = collect_tokens(tok_slug)
        if tok:
            used_tok.add(tok_slug)
        if not runs and not tok:
            continue
        def _faithful(r):
            t = (r.get("meta") or {}).get("temperature")
            return t is None or t <= 1.05
        qual = [r for r in runs if r["qualifies"]]
        f_qual = [r for r in qual if _faithful(r)]
        f_all = [r for r in runs if _faithful(r)]
        # 展示哪一条由 collect_runs 统一决定（primary），保证"被展示的运行一定带 logprobs"
        show = [r for r in runs if r.get("primary")] or runs
        agg = {k: sum(1 for r in runs if r["verdict"]["kind"] == k)
               for k in ("seam", "residue", "clean", "no_content")}
        items.append({"kind": "case", "id": cid, "name": entry["name"],
                      "kindLabel": "人工构造" if entry["kind"] == "controlled" else "历史窗口",
                      "observation": entry["observation"],
                      "note": expected.get(cid, {}).get("verification_note", ""),
                      "evidence": case_evidence(cid, runs, tok, collect_rolls(cid)),
                      "prefill": collect_prefill(cid),
                      "rolls": collect_rolls(cid),
                      "runs": show, "allRuns": runs, "nRepro": len(qual), "nFaithful": len(f_all),
                      "nFaithfulRepro": len(f_qual), "agg": agg, "tok": tok,
                      "aggLabel": (f"复现 {len(qual)}/{len(runs)} 次（已复现切断+token 序列）"
                                   if qual else f"{len(runs)} 次抓取，均未复现切断+token 序列") if runs else "无 chat 抓取"})
    # ② 其余 token 抓取（探针）
    for d in sorted([p for p in TOK.iterdir() if p.is_dir()]) if TOK.exists() else []:
        if d.name.startswith("_") or d.name in used_tok:
            continue
        tok = collect_tokens(d.name)
        if tok:
            items.append({"kind": "token", "id": d.name, "name": "token 探针", "kindLabel": "token 探针",
                          "observation": "原始 token 流", "note": "", "runs": [], "agg": {},
                          "tok": tok, "aggLabel": f"{tok['out']['n']} tokens"})

    data = {
        "meta": {
            "endpoint": os.environ.get("TOKEN_BASE", "http://127.0.0.1:30000"),
            "model": "glm52-fp8-official",
            "chatApi": "/v1/chat/completions（交付 reasoning_content / content，不给 token id）",
            "genApi": "/generate + return_logprob + return_text_in_logprobs（给 output_ids 与每 token 的 id/文本）",
            "specialMap": {str(k): v for k, v in SPECIAL.items()},
            "markerIds": sorted(MARKER_IDS),
        },
        "items": items,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    assets = pathlib.Path(os.environ.get("ASSETS_DIR", str(ROOT / "assets")))
    html = (TEMPLATE.replace("__TREE_CSS__", (assets / "json_tree.css").read_text())
                    .replace("__TREE_JS__", (assets / "json_tree.js").read_text())
                    .replace("__DATA__", payload))
    OUT.write_text(html, encoding="utf-8")
    ncase = len([i for i in items if i["kind"] == "case"])
    ntok = len([i for i in items if i["tok"]])
    print(f"写出 {OUT} ({OUT.stat().st_size/1024:.0f} KB)：{len(items)} 项（case {ncase} / 带 token 数据 {ntok}）")
    for i in items:
        t = i["tok"]
        extra = ""
        if t:
            extra = f" | token: 输出 {t['out']['n']}（特殊 {t['verdict']['special_close'] + t['verdict']['special_open']}）输入 {t['input']['n']}"
        print(f"  · {i['id']:26s} {i['aggLabel']}{extra}")


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>think 标记复现 · 一个页面看全部（请求/响应/接缝/原始 token）</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2128;--line:#30363d;--fg:#e6edf3;--dim:#8b949e;
        --accent:#58a6ff;--mark:#f2cc60;--ok:#7ee787;--reason:#c9a7ff;--content:#7ee787;--bad:#ff7b72;
        --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
  code,pre,.mono{font-family:var(--mono)}
  header{position:sticky;top:0;z-index:30;background:#161b22;border-bottom:1px solid var(--line);padding:9px 16px}
  h1{margin:0 0 3px;font-size:15px}
  .meta{color:var(--dim);font-size:12px}.meta b{color:var(--fg)}
  .layout{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:calc(100vh - 62px)}
  aside{border-right:1px solid var(--line);padding:10px;position:sticky;top:62px;align-self:start;
        max-height:calc(100vh - 62px);overflow:auto}
  aside h2{font-size:11px;color:var(--dim);margin:8px 4px 5px;font-weight:500;text-transform:uppercase}
  aside button{width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);color:var(--fg);
               padding:6px 9px;border-radius:7px;margin-bottom:5px;cursor:pointer;font-size:12px;display:block}
  aside button:hover{border-color:#4d5560}
  aside button.on{background:#1f6feb22;border-color:var(--accent)}
  .cid{font-family:var(--mono);font-size:11.5px}
  .nm{color:var(--dim);font-size:11px;margin-top:2px}
  .tag{display:inline-block;font-size:10px;padding:0 5px;border-radius:8px;font-family:var(--mono);margin-right:4px}
  .t-seam{background:#ff7b7222;color:var(--bad);border:1px solid #ff7b7244}
  .t-ok{background:#8b949e22;color:var(--dim);border:1px solid #8b949e44}
  .t-tok{background:#f2cc6022;color:var(--mark);border:1px solid #f2cc6044}
  .t-lp{background:#58a6ff22;color:var(--accent);border:1px solid #58a6ff44}
  .t-repro{background:#7ee78722;color:var(--ok);border:1px solid #7ee78744}
  .tk.reason{background:#c9a7ff1f;border-color:#c9a7ff44}
  .tk.content{background:#7ee7871f;border-color:#7ee78744}
  .tk.boundary{background:#ff7b7255;border-color:#ff7b72;color:#fff;font-weight:700}
  .tk.gone{opacity:.55;text-decoration:line-through}
  .fldbar{display:flex;gap:2px;flex-wrap:wrap;margin:6px 0}
  .fldbar span{font-family:var(--mono);font-size:11px;padding:1px 6px;border-radius:4px}
  main{padding:12px 16px 80px;min-width:0}
  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
  .bar button{background:var(--panel2);border:1px solid var(--line);color:var(--fg);padding:5px 11px;
              border-radius:6px;cursor:pointer;font-size:12.5px}
  .bar button:hover{border-color:var(--accent)}
  .bar button.on{background:#1f6feb22;border-color:var(--accent)}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px;min-width:0}
  .card h2{margin:0 0 8px;font-size:13.5px}
  .card h2 small{color:var(--dim);font-weight:400;font-family:var(--mono);margin-left:8px}
  .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:8px}
  .k{color:var(--dim);font-size:12px}.k b{color:var(--fg)}
  pre{background:#0b0f14;border:1px solid var(--line);border-radius:6px;padding:11px;margin:0;
      white-space:pre-wrap;word-break:break-word;font-size:12.5px;max-height:56vh;overflow:auto}
  table{border-collapse:collapse;font-size:12.5px;width:100%}
  th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}
  th{background:var(--panel2);color:var(--dim);font-weight:500}
  td.m{font-family:var(--mono);font-size:11.5px;word-break:break-all}
  .r{color:var(--reason)} .c{color:var(--content)} .bad{color:var(--bad)} .ok{color:var(--ok)}
  /* token 网格 */
  #gridOut,#gridIn,#gridLp{display:flex;flex-wrap:wrap;gap:2px;background:#0b0f14;border:1px solid var(--line);
        border-radius:6px;padding:8px;max-height:44vh;overflow:auto;align-content:flex-start;min-width:0}
  .tk{font-family:var(--mono);font-size:12px;padding:2px 4px;border-radius:4px;cursor:pointer;
      border:1px solid #21262d;background:#12161d;white-space:pre;min-width:14px;text-align:center;
      max-width:100%;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto}
  .tk:hover{outline:2px solid var(--accent);outline-offset:-2px}
  .tk.sel{outline:2px solid var(--mark);outline-offset:-2px}
  .tk.sp{background:#ff7b7233;border-color:#ff7b72;color:#ffb3ad;font-weight:700}
  .tk.marker{background:#ff7b7255;border-color:#ff7b72;color:#fff;font-weight:700}
  .tk.sys{background:#c9a7ff22;border-color:#c9a7ff66;color:#d7bcff}
  .tk.ws{color:#484f58} .tk.bytes{color:#79c0ff}
  /* 标记高亮：special token 形态 vs 文本形态（颜色刻意不同） */
  .tk.mk-special{background:#ff4d4f66 !important;border-color:#ff4d4f !important;color:#fff !important;
                 box-shadow:inset 0 0 0 2px #ff4d4faa !important}
  .tk.mk-text{background:#f2cc604d !important;border-color:#f2cc60 !important;color:#ffe9a8 !important;
              box-shadow:inset 0 0 0 2px #f2cc60aa !important}
  .tk.mk-glued{text-decoration:underline dotted rgba(255,255,255,.85);text-underline-offset:2px}
  .tk.flash-mk{outline:3px solid var(--accent) !important;outline-offset:1px}
  .legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-top:8px}
  .legend span{display:flex;align-items:center;gap:5px}
  .sw{display:inline-block;width:11px;height:11px;border-radius:3px;border:1px solid var(--line)}
  #tkDetail{background:#0b0f14;border:1px solid var(--accent);border-radius:6px;padding:10px 12px;
            font-family:var(--mono);font-size:12.5px;min-height:74px;white-space:pre-wrap;word-break:break-word}
  mark.tag{background:#f2cc6033;color:var(--mark);border-radius:3px;padding:0 2px}
  mark.del{background:#ff7b7244;color:#ffb3ad;text-decoration:line-through}
  mark.uchip{background:#ff7b7233;color:#ff9d95;border:1px solid #ff7b7266;border-radius:4px;
             padding:0 4px;margin:0 1px;font-size:11px;cursor:help}
  mark.uchip .uc-code{color:#f2cc60;font-weight:700}
  mark.uchip .uc-bytes{color:#8b949e;margin-left:3px;font-size:10px}
  mark.jesce{background:#58a6ff26;color:#79c0ff;border-radius:3px;padding:0 1px}
  .hint{color:var(--dim);font-size:12px;margin-top:8px}
  .evbox{font-size:12px;line-height:1.75;margin:0 0 10px;padding:8px 10px;border-radius:6px;
         background:#0e1219;border:1px solid var(--line)}
  .evbox.t-ok{border-color:#7ee78766} .evbox.t-warn{border-color:#f2cc6066} .evbox.t-bad{border-color:#ff7b7266}
  .evbox ul{margin:4px 0 4px 18px;padding:0} .evbox li{margin:1px 0}
  .tag.t-warn{background:#f2cc6022;border-color:#f2cc60;color:#f2cc60}
  .tag.t-ok{background:#7ee78722;border-color:#7ee787;color:#7ee787}
  .tag.t-bad{background:#ff7b7222;border-color:#ff7b72;color:#ffb3ad}
  .pftext{white-space:pre-wrap;word-break:break-word;font-family:var(--mono);font-size:12px;line-height:1.7;
          max-height:58vh;overflow:auto;background:#0b0f14;border:1px solid var(--line);border-radius:6px;
          padding:10px 12px;margin:6px 0 0}
  .pfs{border-radius:2px}
  .pftpl{background:#c9a7ff26;color:#d7bcff}
  .pfsrc-msg{color:#c9d1d9}
  .pfsrc-tool{background:#58a6ff1f;color:#9ecbff}
  .pfsp-skip{background:#ff4d4f33;box-shadow:inset 0 0 0 1px #ff7b72;font-weight:700;color:#fff}
  .pfsp-added{background:#f2cc6033;box-shadow:inset 0 0 0 1px #f2cc60;font-weight:700;color:#ffe9a8}
  .pftext[data-filter="tpl"] .pfs:not(.pftpl){opacity:.28}
  .pftext[data-filter="req"] .pftpl{opacity:.28}
  .pftext[data-filter="sp"] .pfs:not(.pfsp-skip):not(.pfsp-added){opacity:.12}
  .pfs.pf-req-sp{background:#ff4d4f66 !important;box-shadow:inset 0 0 0 2px #ff2d2d !important;
                 color:#fff !important;font-weight:700 !important}
  .pftext[data-filter="reqsp"] .pfs:not(.pf-req-sp){opacity:.1}
  .pftext[data-filter="plain"] .pfs{background:none !important;box-shadow:none !important;color:inherit !important;font-weight:400 !important}
  .pgrid{display:flex;flex-wrap:wrap;gap:2px;background:#0b0f14;border:1px solid var(--line);
         border-radius:6px;padding:8px;max-height:52vh;overflow:auto;align-content:flex-start;min-width:0}
  .tk.pf-msg{background:#7ee7871f;border-color:#7ee78755}
  .tk.pf-tool{background:#58a6ff1f;border-color:#58a6ff55}
  .tk.pf-tpl{background:#c9a7ff1f;border-color:#c9a7ff44;color:#d7bcff}
  .tk.pf-sp-skip{box-shadow:inset 0 0 0 2px #ff7b72 !important;font-weight:700;color:#fff !important}
  .tk.pf-sp-added{box-shadow:inset 0 0 0 2px #f2cc60 !important;font-weight:700;color:#ffe9a8 !important}
  .pgrid[data-filter="req"] .tk.pf-tpl{opacity:.16}
  .pgrid[data-filter="tpl"] .tk:not(.pf-tpl){opacity:.16}
  .pgrid[data-filter="sp"] .tk[data-sp="0"]{opacity:.12}
  .tk.pf-req-sp{background:#ff4d4f66 !important;box-shadow:inset 0 0 0 2px #ff2d2d !important;
                color:#fff !important;font-weight:700 !important}
  .pgrid[data-filter="reqsp"] .tk:not(.pf-req-sp){opacity:.1}
  .mksum{font-size:12px;line-height:1.8;white-space:normal;overflow:visible;
         margin:6px 0 8px;padding:7px 9px;background:#0e1219;border:1px solid var(--line);border-radius:6px}
  .mksum:empty{display:none}
  .mksum code{font-family:var(--mono);background:#1a2230;border:1px solid var(--line);border-radius:3px;padding:0 3px}
  .mksum .mkjump{color:var(--accent);text-decoration:none;font-family:var(--mono)}
  .mksum .mkjump:hover{text-decoration:underline}
  details{margin-top:8px} summary{cursor:pointer;color:var(--dim);font-size:12.5px}
  .seamview{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .cat{background:#0b0f14;border:1px solid var(--bad);border-radius:6px;padding:10px 12px;font-family:var(--mono);
       font-size:12.5px;white-space:pre-wrap;word-break:break-all}
  @media(max-width:980px){.layout{grid-template-columns:1fr}aside{position:static;max-height:none}
    .seamview{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>think 标记复现 · 一个页面看全部</h1>
  <div class="meta" id="hmeta"></div>
</header>
<div class="layout">
  <aside><div id="list"></div>
    <h2>特殊 token 图谱</h2>
    <div class="mono" id="spmap" style="font-size:10.5px;line-height:1.7;color:#8b949e"></div>
  </aside>
  <main>
    <div class="card" id="info"></div>
    <div class="bar" id="tabs"></div>
    <div id="pane"></div>
  </main>
</div>
<style>__TREE_CSS__</style>
<script>__TREE_JS__</script>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const bytes = s => [...new TextEncoder().encode(s)];
const hexb = s => bytes(s).map(b => b.toString(16).padStart(2,'0')).join(' ');
const printable = s => bytes(s).every(b => b === 9 || b === 10 || (b >= 32 && b < 127));
window.__hl = s => esc(s)
  .replace(/&lt;\/think&gt;/g,'<mark class="tag">&lt;/think&gt;</mark>')
  .replace(/&lt;think&gt;/g,'<mark class="tag">&lt;think&gt;</mark>')
  .replace(/&lt;\|[a-z_]+\|&gt;/g, m => '<mark class="tag">' + m + '</mark>')
  .replace(/\\n/g,'<mark class="jesce">\\n</mark>');

document.getElementById('hmeta').innerHTML =
  `<b>${esc(D.meta.endpoint)}</b> · model <b>${esc(D.meta.model)}</b>` +
  ` · chat: <b>${esc(D.meta.chatApi)}</b> · token: <b>${esc(D.meta.genApi)}</b>`;
document.getElementById('spmap').innerHTML =
  Object.entries(D.meta.specialMap).map(([id,t]) => `${esc(t)} = ${id}`).join('<br>');

let cur = 0, runIdx = 0, view = 'req', showAll = false;
function runsOf(it) { return (showAll && it.allRuns) ? it.allRuns : (it.runs || []); }
const TABS = [
  ['req','① 请求 / prompt'],
  ['res','② 响应（chat 交付）'],
  ['lp','③ token 流（本次响应 · logprobs）'],
  ['tok','④ /generate 原始 token（另一路）'],
  ['tokin','⑤ 输入 token 网格'],
  ['verdict','⑥ 判定 · 丢弃 · 对照'],
  ['prefill','⑨ 输入 prefill 溯源（真实 chat template）'],
  ['seam','⑦ 接缝（字段边界）'],
  ['hdr','⑧ 头部'],
];
const list = document.getElementById('list');
function badge(it) {
  const tags = [];
  if (it.runs && it.runs.length) tags.push(`<span class="tag ${it.agg && it.agg.seam ? 't-seam' : 't-ok'}">${it.runs.length} 次</span>`);
  if (it.nRepro) tags.push(`<span class="tag t-repro">已复现 ×${it.nRepro}</span>`);
  else if ((it.allRuns || []).length) tags.push(`<span class="tag t-ok">未复现</span>`);
  if (it.tok) tags.push(`<span class="tag t-tok">/generate ${it.tok.out.n}</span>`);
  if (it.evidence) tags.push(`<span class="tag ${it.evidence.cls}">${esc(it.evidence.tag)}</span>`);
  if (it.prefill) tags.push(`<span class="tag t-tok">prefill ${it.prefill.stats.n}</span>`);
  return tags.join('');
}
D.items.forEach((it, i) => {
  const b = document.createElement('button');
  b.innerHTML = `<div class="cid">${esc(it.id)}</div>
     <div class="nm">${esc(it.name)} · ${esc(it.observation)}</div>${badge(it)}`;
  b.onclick = () => { cur = i; runIdx = 0; showAll = false; view = (it.runs || []).length ? 'req' : 'tok'; refreshList(); };
  list.append(b);
});
function refreshList() {
  [...list.children].forEach((x, j) => x.classList.toggle('on', j === cur));
  renderTabs(); render();
}
function tabsFor(it) {
  const rs = runsOf(it);
  return TABS.filter(([k]) =>
    (k === 'req' && (rs.length || it.tok)) ||
    (k === 'res' && (rs.length || (it.tok && (it.tok.chat.reasoning || it.tok.chat.content)))) ||
    (k === 'lp' && rs.some(r => r.logprobs)) ||
    (k === 'tok' && it.tok) || (k === 'tokin' && it.tok && it.tok.input.n) ||
    (k === 'verdict' && it.tok) ||
    (k === 'prefill' && it.prefill) ||
    (k === 'seam' && (it.runs.length || it.tok)) ||
    (k === 'hdr' && rs.length));
}
function renderTabs() {
  const it = D.items[cur], tabs = tabsFor(it), el = document.getElementById('tabs');
  const rs = runsOf(it);            // ← 点击标签时要用它判断"当前 run 有没有 logprobs"
  el.innerHTML = '';
  if (!tabs.some(t => t[0] === view)) view = tabs[0][0];
  tabs.forEach(([k, label]) => {
    const b = document.createElement('button');
    b.textContent = label; b.className = k === view ? 'on' : '';
    b.onclick = () => {
      view = k;
      if (k === 'lp' && rs.length && !(rs[runIdx] || {}).logprobs) {
        const i = rs.findIndex(r => r.logprobs);
        if (i >= 0) runIdx = i;                 // 自动切到带 token 流的运行
      }
      renderTabs(); render();
    };
    el.append(b);
  });
}

function endBadge(m) {
  if (m.finish === 'length') return `<span style="color:var(--bad)">⏸ 达到 max_tokens 被截断（length）</span>`;
  if (m.finish === 'tool_calls') return `<span style="color:var(--ok)">⏹ 正常结束：模型转为工具调用（tool_calls）</span>`;
  if (m.finish === 'stop') return `<span style="color:var(--ok)">⏹ 正常结束（stop）</span>`;
  return esc(String(m.finish));
}
/* ===== 一键标出 </think> / <think>：区分「文本形态」与「special token 形态」=====
   要点：文本形态不一定是 3 个 token —— 可能是 1 个普通 token、2 个、5 个，
   也可能与外面的引号/反引号/**代码围栏**粘在同一个 token 里（如 token 文本 = "`</think>`"）。
   所以不能按 token 边界贪心对齐，必须先把所有 token 拼成整串，做子串查找，再按字符区间映射回 token。 */
const SPECIAL_TXT2ID = Object.fromEntries(Object.entries(D.meta.specialMap).map(([id, t]) => [t, +id]));
const SPECIAL_ID2TXT = Object.fromEntries(Object.entries(D.meta.specialMap).map(([id, t]) => [+id, t]));
const THINK_MARKS = ['</think>', '<think>'];

function tokenIndex(tokens) {                        // 全串 + 每个 token 的字符区间
  let full = ''; const spans = [];
  for (const t of tokens) { const x = t.text || ''; spans.push([full.length, full.length + x.length]); full += x; }
  return { full, spans };
}
/* 在整个 token 序列里找全部标记出现处（按字符，不看 token 边界） */
function scanMarkers(tokens, patterns, idx) {
  const { full, spans } = idx, out = [];
  for (const M of patterns) {
    let from = 0, at;
    while ((at = full.indexOf(M, from)) !== -1) {
      from = at + M.length;
      const s0 = at, s1 = at + M.length;
      let i = 0; while (i < spans.length && spans[i][1] <= s0) i++;
      if (i >= spans.length) break;
      let j = i; while (j + 1 < spans.length && spans[j][1] < s1) j++;
      const gH = s0 > spans[i][0], gT = s1 < spans[j][1];
      const single = (i === j), tk = tokens[i];
      const exact = single && !gH && !gT;            // 这个 token 的文本正好等于标记
      let kind = 'text';
      if (exact && (tk.special || SPECIAL_TXT2ID[tk.text] !== undefined)) kind = 'special';
      out.push({ text: M, start: i, end: j, len: j - i + 1, kind,
                 glued: gH || gT, gluedHead: gH, gluedTail: gT, exact,
                 tokText: tokens.slice(i, j + 1).map(x => x.text || '').join(''),
                 ids: tokens.slice(i, j + 1).map(x => x.id), charAt: s0 });
    }
  }
  out.sort((a, b) => a.start - b.start || b.len - a.len);
  const taken = new Set(), keep = [];                 // 重叠去重：同一段 token 只留一条
  for (const h of out) {
    let overlap = false;
    for (let k = h.start; k <= h.end; k++) if (taken.has(k)) overlap = true;
    if (!overlap) { keep.push(h); for (let k = h.start; k <= h.end; k++) taken.add(k); }
  }
  return keep;
}
function applyMarkMarks(container, tokens, idx, { think = false, allSpecial = false } = {}) {
  [...container.children].forEach(c => c.classList.remove('mk-special', 'mk-text', 'mk-glued'));
  const hits = [];
  if (think) hits.push(...scanMarkers(tokens, THINK_MARKS, idx));
  if (allSpecial) hits.push(...scanMarkers(tokens, Object.keys(SPECIAL_TXT2ID), idx));
  const uniq = new Map(); hits.forEach(h => uniq.set(h.start + '-' + h.end, h));
  const list = [...uniq.values()].sort((a, b) => a.start - b.start);
  list.forEach(h => {
    for (let k = h.start; k <= h.end; k++) {
      const cell = container.children[k];
      if (cell) cell.classList.add(h.kind === 'special' ? 'mk-special' : 'mk-text');
    }
    // 胶水只标真正含多余额外字符的那一格（头/尾）
    if (h.gluedHead) { const c = container.children[h.start]; if (c) c.classList.add('mk-glued'); }
    if (h.gluedTail) { const c = container.children[h.end];   if (c) c.classList.add('mk-glued'); }
  });
  return list;
}
/* 标记里含引号/反引号等"胶水"字符时单独点出来（标记可能与引号/代码围栏粘在同一 token 内） */
function glueNote(h) {
  if (!h.glued) return '';
  const parts = h.tokText.split(h.text);
  const head = parts[0], tail = parts.slice(1).join(h.text);
  return '　<b>粘连</b>：<code>' + esc(head) + '</code>'
       + '<b style="color:inherit">' + esc(h.text) + '</b>'
       + '<code>' + esc(tail) + '</code>';
}
function markSummary(list) {
  if (!list.length) return '（未发现标记）';
  const byMark = {};
  list.forEach(h => (byMark[h.text] = byMark[h.text] || []).push(h));
  const total = `<b>标记：共 ${list.length} 处</b>`;
  const groups = Object.entries(byMark).map(([m, hs]) => {
    const sp = hs.filter(h => h.kind === 'special').length, tx = hs.length - sp;
    return `${esc(m)} ${hs.length} 处（`
      + (sp ? `<b style="color:#ff9d95">special token 形态 ${sp}</b>` : '')
      + (sp && tx ? ' · ' : '')
      + (tx ? `<b style="color:#f2cc60">文本形态 ${tx}</b>` : '') + '）';
  }).join('　｜　');
  // 详情：按序号排，但 special 形态一律保留（不被截断吃掉）
  const head = list.slice(0, 14);
  const rest = list.slice(14);
  const keep = head.concat(rest.filter(h => h.kind === 'special'));
  const detail = keep.map(h =>
      `<a href="#" class="mkjump" data-start="${h.start}">#${h.start}${h.len > 1 ? '-' + h.end : ''}</a> `
      + `<span style="color:${h.kind === 'special' ? '#ff9d95' : '#f2cc60'}">${esc(h.text)}</span>`
      + `<span class="k">（${h.kind === 'special'
            ? 'special token ' + (SPECIAL_TXT2ID[h.text] != null ? SPECIAL_TXT2ID[h.text] : (h.ids[0] != null ? h.ids[0] : '?'))
            : (h.len === 1 ? '1 个普通 token' : h.len + ' 个 token')}）</span>`
      + glueNote(h)).join('<br>');
  const more = rest.length ? `<br><span class="k">…另有 ${rest.length} 处未列出（点上方按钮可清除标记）</span>` : '';
  return total + '　｜　' + groups + '<br>' + detail + more;
}
function wireMarks(container, tokens, bar, sumEl) {
  if (!container || !bar || !sumEl) return;
  const idx = tokenIndex(tokens);
  const bT = document.createElement('button'); bT.textContent = '🔍 标出 </think> / <think>';
  const bA = document.createElement('button'); bA.textContent = '🔍 标出所有特殊 token';
  const bC = document.createElement('button'); bC.textContent = '清除标记';
  let st = { think: false, allSpecial: false };
  const upd = () => {
    bT.classList.toggle('on', st.think); bA.classList.toggle('on', st.allSpecial);
    const list = applyMarkMarks(container, tokens, idx, st);
    sumEl.innerHTML = markSummary(list);
    sumEl.classList.toggle('on', list.length > 0);
    sumEl.querySelectorAll('.mkjump').forEach(a => a.onclick = (e) => {
      e.preventDefault();
      const c = container.children[+a.dataset.start];
      if (c) { c.scrollIntoView({ block: 'center' }); c.classList.add('flash-mk'); setTimeout(() => c.classList.remove('flash-mk'), 1500); }
    });
  };
  bT.onclick = () => { st.think = !st.think; upd(); };
  bA.onclick = () => { st.allSpecial = !st.allSpecial; upd(); };
  bC.onclick = () => { st = { think: false, allSpecial: false }; upd(); };
  bar.append(bT, bA, bC);
  return upd;
}

/* 「模型实际看到的 prompt」：模板渲染后的完整文本，按来源着色（字符区间来自 tokenizer offset_mapping） */
function promptViewCard(pf) {
  const c = document.createElement('div'); c.className = 'card';
  const V = pf.verification, S = pf.stats;
  c.innerHTML = `<h2>模型实际看到的 prompt <small>chat template 渲染后的 prefill 文本 · ${S.n} token · ${S.chars} 字符</small></h2>
    <div class="evbox t-ok">
      <b>✅ 渲染 ${V.rendered_tokens} token == 端点 <code>usage.prompt_tokens</code> ${V.endpoint_prompt_tokens}</b>
      <ul>
        <li>模板：<code>${esc(V.template)}</code>（启动参数里没有 <code>--chat-template</code> 覆盖，<code>tokenizer_config.json</code> 也没有它）</li>
        <li>把渲染串重新 tokenize，ids 与模板输出逐位一致：<code>${V.ids_retok_match}</code>；每个 token 的字符区间来自 tokenizer 的 <code>offset_mapping</code></li>
        <li>本次模板参数：<code>${esc(JSON.stringify(V.kwargs))}</code>${V.kwargs && V.kwargs.reasoning_effort ? '（<b>请求里的 reasoning_effort 会被转发进模板</b>，写成 <code>&lt;|system|&gt;Reasoning Effort: ' + esc(String(V.kwargs.reasoning_effort).charAt(0).toUpperCase() + String(V.kwargs.reasoning_effort).slice(1)) + '</code>）' : ''}</li>
      </ul>
    </div>
    ${S.n_special_in_req ? `<div class="evbox t-bad"><b>⚠️ 请求内容里有 ${S.n_special_in_req} 个真正的 special token</b>
      （${Object.entries(S.special_in_req).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<code>${esc(k)}</code>×${v}`).join('　')}）
      —— 它们在下面用<b>红底白字</b>标出：历史消息里写这些字符串，会以<b>控制符</b>身份进输入，不是普通文本。</div>` : ''}
    <div class="bar" id="ptBar"></div>
    <div class="legend">
      <span><i class="sw" style="background:#c9a7ff26;border-color:#c9a7ff66"></i>chat template 注入（角色标记 / effort 行 / tools preamble / <code>&lt;think&gt;</code> 包裹 / 工具调用包裹）</span>
      <span><i class="sw" style="background:#0b0f14;border-color:#30363d"></i>请求内容 · 消息正文</span>
      <span><i class="sw" style="background:#58a6ff1f;border-color:#58a6ff"></i>请求内容 · 工具定义 / 工具调用参数</span>
      <span><i class="sw" style="background:#ff4d4f33;border-color:#ff7b72"></i>special token（skip 会清）</span>
      <span><i class="sw" style="background:#f2cc6033;border-color:#f2cc60"></i>added token（不会被清）</span>
      <span><i class="sw" style="background:#ff4d4f66;border-color:#ff2d2d"></i><b>请求内容里的 special token</b>（历史消息自带的控制符）</span>
    </div>
    <pre class="pftext" id="ptText"></pre>
    <div class="hint">悬停任意片段可看到：第几个 token、id、来自哪里。这是<b>模型真正吃进去的文本</b>；上面那份 JSON 是<b>我们发出去的字段</b>。</div>`;
  const pre = c.querySelector('#ptText'), A = pf.tokens, P = pf.prompt;
  let html = '';
  for (let i = 0; i < A.ids.length; i++) {
    const s0 = A.st[i], e0 = (i + 1 < A.st.length ? A.st[i + 1] : P.length);
    if (e0 <= s0) continue;                        // 零宽 token（多字节被切开）不占字符
    const [src, detail] = A.labels[A.lab[i]].split('|');
    const sp = A.sp[i];
    const cls = 'pfs ' + (sp === 2 ? 'pfsp-skip ' : sp === 1 ? 'pfsp-added ' : '')
      + (src === 'tpl' ? 'pftpl' : (detail.startsWith('msg') ? 'pfsrc-msg' : 'pfsrc-tool'))
      + (sp && src === 'req' ? ' pf-req-sp' : '');
    html += '<span class="' + cls + '" title="#' + i + ' id=' + A.ids[i] + ' · ' +
      (src === 'tpl' ? '模板注入（' + detail + '）' : '请求内容（' + detail + '）') +
      (sp ? (sp === 2 ? ' · special token，skip_special_tokens 会清' : ' · added token，不会被清') : '') +
      '">' + esc(P.slice(s0, e0)) + '</span>';
  }
  pre.innerHTML = html;
  const bar = c.querySelector('#ptBar');
  [['all', '全部着色'], ['tpl', '只高亮模板注入'], ['req', '只高亮请求内容'], ['sp', '只看 special token'],
   ['reqsp', '只看 请求内容里的 special token'], ['plain', '原始文本（不着色）']]
    .forEach(([m, l]) => {
      const b = document.createElement('button'); b.textContent = l; b.dataset.mode = m;
      b.onclick = () => { pre.dataset.filter = m; [...bar.children].forEach(x => x.classList.toggle('on', x.dataset.mode === m)); };
      bar.append(b);
    });
  pre.dataset.filter = 'all';
  return c;
}

function paneCard(title, bl, opts = {}) {
  let isJson = true; try { JSON.parse(bl.text); } catch (e) { isJson = false; }
  if (isJson) return jsonPane(bl, { title, extra: opts.extra || '', hl: window.__hl, openDepth: 2,
    focus: /body\.json/.test(bl.name) ? ['message','reasoning_content','content','tool_calls','arguments']
                                      : ['messages','content','tools','parameters'] });
  const card = document.createElement('div'); card.className = 'card';
  const hd = document.createElement('div'); hd.className = 'hd';
  hd.innerHTML = `<h2>${esc(title)}</h2><span class="k"><b>${bl.bytes}</b> 字节 · ${esc(bl.name)}</span>`;
  const pre = document.createElement('pre');
  pre.className = 'raw-pre'; pre.textContent = bl.text;
  pre.dataset.expectBytes = bl.bytes; pre.dataset.expectSha = bl.sha256; pre.dataset.name = bl.name;
  const bar = document.createElement('div'); bar.className = 'bar';
  const dl = document.createElement('button'); dl.textContent = '下载原文';
  dl.onclick = () => { const b = new Blob([bl.text], {type:'application/octet-stream'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = bl.name; a.click(); };
  bar.append(dl);
  card.append(hd, bar, pre);
  return card;
}

function shortToken(v, max = 40) {          // 超长 token 只显示头尾，完整内容在悬停详情里
  if (v === null || v === undefined) return v;
  if (printable(v) && v.length > max) return v.slice(0, max - 12) + '…(+' + (v.length - max + 12) + ')';
  return v;
}
function tokenGrid(container, toks, total, detailEl, label) {
  container.innerHTML = '';
  toks.forEach((t, i) => {
    const v = t.text === null ? `#${t.id}` : t.text;
    const cls = [];
    if (t.marker) cls.push('marker'); else if (t.special) cls.push('sp');
    else if (/^<\|.*\|>$/.test(v || '')) cls.push('sys');
    else if (/^\s+$/.test(v || '')) cls.push('ws');
    else if (!printable(v || '')) cls.push('bytes');
    const show = t.special || t.marker ? (t.special || v)
               : (printable(v || '') ? (v === '' ? '∅' : shortToken(v))
                  : (bytes(v || '').length <= 12 ? hexb(v || '') : '⋯' + bytes(v || '').length + 'B'));
    const el = document.createElement('span');
    el.className = 'tk ' + cls.join(' ');
    el.textContent = show;
    el.title = `${label} #${i}  id=${t.id}` + (t.special ? `  ← 特殊 token ${t.special}` : '');
    const show2 = () => {
      detailEl.textContent =
        [`来源       : ${label}`,
         `位置       : #${i} / ${total}`,
         `token id   : ${t.id}` + (t.special ? `   （特殊 token：${t.special}）` : '   （普通 token）'),
         `token 文本 : ${JSON.stringify(t.text)}`,
         `UTF-8 字节 : ${bytes(t.text || '').length} 字节  ${hexb(t.text || '')}`,
         `可打印 ASCII: ${printable(t.text || '') ? '是' : '否'}`,
         `logprob    : ${t.logprob}`,
         t.marker ? '★ think 标记本身：服务端会按“控制信号”处理它（切字段 / 丢弃）'
                  : (t.special ? '★ 其它特殊 token' : '（普通文本 token，服务端当字符处理）')].join('\n');
      [...container.children].forEach(x => x.classList.remove('sel')); el.classList.add('sel');
    };
    el.onmouseenter = el.onclick = show2;
    container.append(el);
  });
}

function render() {
  const it = D.items[cur], runs = runsOf(it), run = runs[runIdx] || null, t = it.tok;
  // 概要
  let info = `<div class="hd"><h2>${esc(it.id)} · ${esc(it.name)}</h2>
      <span class="k">${esc(it.kindLabel)} · 观察点：${esc(it.observation)}</span></div>`;
  if (it.evidence) {
    info += `<div class="evbox evid ${it.evidence.cls}" id="evid"><b>${esc(it.evidence.tag)}</b>
      <span class="k">${esc(it.evidence.note)}</span>
      <ul>${it.evidence.parts.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      <div class="k">判据：边界被消费的标记若是<b>文本形态</b> ⇒ 吞掉的是正文里真实存在的字符（<b>有损</b>）；
        若是 <b>special 单 token</b>（154842）⇒ 本来就不属于文本（<b>零损失</b>，只剩分界落点）。</div></div>`;
  }
  if (it.allRuns && it.allRuns.length) {
    info += `<div class="hint">抓取情况：共 ${it.allRuns.length} 次运行，其中 <b>${it.nRepro}</b> 次同时复现了切断并带 token 序列
      —— ${it.nRepro ? '页面默认只展示这些；' : '（本页仍展示全部，便于判断）；'}
      <label style="margin-left:6px"><input type="checkbox" id="chkAll"${showAll ? ' checked' : ''}> 显示全部运行（含未复现的）</label></div>`;
  }
  if (run) {
    const m = run.meta;
    info += `<table>
      <tr><th>本次运行</th><td class="m">${esc(run.label)}${run.qualifies ? ' · ✅ 已复现切断（且带 token 序列）' : ''}${run.variant ? ' · ⚠️ 变体' : ''}</td>
          <th>结束方式</th><td class="m">${endBadge(m)}</td></tr>
      <tr><th>输出预算</th><td class="m">${esc(String(m.completion_tokens))} / ${esc(String(m.max_tokens))} tokens
            （其中 reasoning ${esc(String(m.reasoning_tokens))}）
            <div class="hint" style="margin-top:2px">左侧 ${esc(String(m.max_tokens))} 是<b>本次请求里我们要的 <code>max_tokens</code></b>
              （抓取脚本自己设的，不是模型上限）；该模型上下文 <b>1,048,576</b> tokens，服务端实测接受 <code>max_tokens</code> ≥ 262144。</div></td>
          <th>reasoning / content</th><td class="m">${m.reasoning_chars} / ${m.content_chars} 字</td></tr>
      <tr><th>客观结论</th><td class="m" style="color:${run.verdict.kind === 'seam' ? 'var(--bad)' : 'var(--fg)'}">${esc(run.verdict.label)}</td></tr>
    </table>
    ${(m.temperature != null && m.temperature > 1.05) ? `<div class="evbox t-warn"><b>⚠️ 高温度变体：本次请求 temperature = ${m.temperature}</b>
      <ul><li>该模型 <code>generation_config.json</code> 默认 <b>1.0 / top_p 0.95</b>；温度 1.2 会让采样跑出分布外文本。</li>
      <li>实测对照（同 case 同 prompt）：1.2 → 平均 logprob <b>−2.5 ~ −10</b>；1.0 → <b>−0.5 ~ −0.6</b>。
        "词中间断句 / content 为空 / 词沙拉"多由高温放大产生。</li>
      <li>读数时请以 <b>temperature = 1.0</b> 的运行作准（本 case 的额外 roll 见上方 roll 统计）。</li></ul></div>` : ''}`;
  }
  if (t) {
    const v = t.verdict;
    info += `<div class="hint">token 抓取（/generate，与 chat 是**另一次**生成）：输出 ${t.out.n} tokens ·
      其中 <b class="bad">&lt;/think&gt; 特殊 token × ${v.special_close}</b>、&lt;think&gt; 特殊 token × ${v.special_open}；
      文本里字面 &lt;/think&gt; ${v.literal_close} 次 · 输入 ${t.input.n} tokens</div>`;
  }
  if (run && run.meta.truncated) info += `<div class="hint" style="color:var(--bad)">⚠️ <b>本次达到输出预算被截断</b>
    （finish=<code>length</code>，${esc(String(run.meta.completion_tokens))}/${esc(String(run.meta.max_tokens))} tokens，其中思考就占了
    ${esc(String(run.meta.reasoning_tokens))}）—— 所以 <code>content</code> 末尾看起来"没说完"是<b>预算用尽</b>造成的，
    <b>不是</b>标记切断造成的（工具包也明确警告不要把截断归因于标签解析）。本次的"切断"证据在
    <b>边界 token</b> 处（③ token 流 标签页），与末尾截断无关。</div>`;
  if (run && run.variant) info += `<div class="hint" style="color:var(--mark)">⚠️ 这是<b>变体</b>：${esc(run.variantNote)}<br>
    原 prompt 下多次抓取未出现"可见切断"（模型总在句末才发出标记），因此用一条明确标注的变体来复现。</div>`;
  if (it.note) info += `<div class="hint">核验要点（工具包）：${esc(it.note)}</div>`;
  document.getElementById('info').innerHTML = info;
  const ca = document.getElementById('chkAll');
  if (ca) ca.onchange = () => { showAll = ca.checked; runIdx = 0; render(); };

  const pane = document.getElementById('pane');
  pane.innerHTML = '';
  if (runs.length) {
    const rb = document.createElement('div'); rb.className = 'bar';
    runs.forEach((r, i) => {
      const b = document.createElement('button');
      b.textContent = `${r.qualifies ? '✅ ' : ''}${r.label} · ${r.verdict.kind === 'seam' ? '切断' : r.verdict.kind === 'no_content' ? 'content 空' : r.verdict.kind === 'residue' ? '残留' : '干净'}${r.logprobs ? ' · token流' : ''}`;
      b.className = i === runIdx ? 'on' : '';
      b.onclick = () => { runIdx = i; render(); };
      rb.append(b);
    });
    if (it.allRuns && it.allRuns.length && run) {
      const b = document.createElement('button');
      b.textContent = showAll ? '只看已复现的运行' : `显示全部运行（${it.allRuns.length}）`;
      b.onclick = () => { showAll = !showAll; runIdx = 0; render(); };
      rb.append(b);
    }
    pane.append(rb);
  }

  if (view === 'req') {
    if (it.prefill) {
      const c = document.createElement('div'); c.className = 'card';
      c.innerHTML = `<h2>我们发出去的请求 <small>OpenAI 形状：messages + tools + 采样参数</small></h2>
        <div class="hint">下面是逐字节发出去的请求体（原始面板为准）；<b>模型看到的不是它</b> ——
          服务端会用 <code>chat_template.jinja</code> 把 messages/tools 重新渲染成一段 prompt（下一块）。</div>`;
      pane.append(c);
    }
    if (run) pane.append(paneCard('完整请求体', run.request, { extra: 'curl --data-binary 直接发送的内容（原始面板为准）' }));
    if (it.prefill) pane.append(promptViewCard(it.prefill));
    if (t) pane.append(paneCard('我们构造的 /generate 文本（对照用，不是 chat 端点的渲染结果）', {
      name: 'prompt.txt', text: t.wrapped || t.prompt, bytes: new TextEncoder().encode(t.wrapped || t.prompt).length,
      sha256: '-' }));
  } else if (view === 'res') {
    if (run) {
      pane.append(paneCard('完整响应体', run.response));
      if (run.toolCalls.length) {
        const c = document.createElement('div'); c.className = 'card';
        c.innerHTML = `<h2>工具调用（结构化通道）</h2>` + run.toolCalls.map((tc, i) => {
          let p = tc.arguments; try { p = JSON.stringify(JSON.parse(tc.arguments), null, 2); } catch (e) {}
          return `<div class="k">#${i} ${esc(tc.name)} · ${(tc.arguments||'').length} 字节</div><pre>${esc(p)}</pre>`;
        }).join('');
        pane.append(c);
      }
      if (run.checks) {
        const c = document.createElement('div'); c.className = 'card';
        c.innerHTML = `<h2>工具包自带判定 <small>${esc(run.checks.name)}</small></h2><pre>${esc(JSON.stringify(run.checks.report, null, 2))}</pre>`;
        pane.append(c);
      }
    }
    if (t && (t.chat.reasoning || t.chat.content)) {
      const c = document.createElement('div'); c.className = 'card';
      c.innerHTML = `<h2>chat 端点交付的字段 <small>token 抓取那次的 chat 结果</small></h2>
        <div class="seamview"><div><div class="k">reasoning_content 尾部</div><pre>${esc(t.chat.reasoning.slice(-300))}</pre></div>
        <div><div class="k">content 开头</div><pre>${esc(t.chat.content.slice(0, 300))}</pre></div></div>
        <div class="hint">finish=${esc(String(t.chat.finish))} · usage=${esc(JSON.stringify(t.chat.usage))}</div>`;
      pane.append(c);
    }
  } else if (view === 'lp') {
    const L = run && run.logprobs;
    const c = document.createElement('div'); c.className = 'card';
    if (!L) {
      c.innerHTML = `<h2>本次响应的 token 流</h2><div class="hint">这个 run 抓取时没有开启 <code>logprobs</code>。
        切到带 <b>token流</b> 标记的运行（如 <code>tok</code>）即可看到。</div>`;
      pane.append(c);
    } else {
      c.innerHTML = `<h2>本次响应的 token 流 <small>同一次生成 · logprobs 逐 token（reasoning + content 都在里面）</small></h2>
        <div class="fldbar">
          <span style="background:#c9a7ff22;color:#c9a7ff">思考 ${L.boundary === null ? '?' : L.boundary} 个</span>
          <span style="background:#ff7b7255;color:#fff">边界 token：${esc(JSON.stringify(L.boundary_text))}</span>
          <span style="background:#7ee78722;color:#7ee787">正文 ${L.boundary === null ? '?' : L.n - L.boundary - 1} 个</span>
          <span style="background:#8b949e22;color:#8b949e">共 ${L.n} 个 token</span>
          <span style="background:#ff7b7233;color:#ffb3ad">被消费 ${L.dropped.length} 个</span>
        </div>
        <div class="bar" id="mkBarLp"></div>
        <div class="mksum" id="mkSum"></div>
        <div id="gridLp"></div>
        <div class="legend">
          <span><i class="sw" style="background:#c9a7ff1f;border-color:#c9a7ff44"></i>思考侧 token（拼接后 == reasoning_content）</span>
          <span><i class="sw" style="background:#ff7b7255;border-color:#ff7b72"></i>边界 token（就是它触发的字段切换）</span>
          <span><i class="sw" style="background:#7ee7871f;border-color:#7ee78744"></i>正文侧 token</span>
          <span><i class="sw" style="background:#12161d;opacity:.55"></i>被消费/丢弃（划掉）</span>
          <span><i class="sw" style="background:#ff4d4f55;border-color:#ff4d4f"></i>标记的 <b>special token 形态</b>（单 token）</span>
          <span><i class="sw" style="background:#f2cc6040;border-color:#f2cc60"></i>标记的 <b>文本形态</b>（1 个或多个普通 token 拼出，也可能与引号/反引号粘在同一 token 内）</span>
        </div>
        <div class="hint">对齐方式：把 token 依次拼接，前 k 个正好等于 <code>reasoning_content</code> → 边界就在第 k 个；
          再把「全部 token 拼接」与 <code>reasoning_content+content</code> 对比，差集就是被服务端消费掉的 token。
          验证：前 k 个拼接 === reasoning_content = <b>${L.boundary !== null}</b>，
          全部拼接 === 两字段之和 = <b>${L.joined_equals}</b>。</div>
        <h2 style="margin-top:12px">被消费/丢弃的 token</h2>
        <pre id="droppedLp">${L.dropped.length ? L.dropped.map(d => `<mark class="del">${esc(d.text)}</mark>  ← ${esc(d.kind)}`).join('\n') : '<span class="k">（无）</span>'}</pre>
        <h2 style="margin-top:12px">选中 token 详情</h2><div id="tkDetail3">把鼠标放到任意格子上…</div>`;
      pane.append(c);
      const droppedTexts = L.dropped.map(d => d.text);
      const cont = c.querySelector('#gridLp');
      L.tokens.forEach((t, i) => {
        const v = t.text ?? '';
        const isGone = droppedTexts.includes(v);
        const cls = [];
        if (t.field === 'boundary') cls.push('boundary'); else if (t.field === 'reasoning') cls.push('reason');
        else if (t.field === 'content') cls.push('content');
        if (isGone) cls.push('gone');
        const el = document.createElement('span');
        el.className = 'tk ' + cls.join(' ');
        el.textContent = printable(v) ? (v === '' ? '∅' : shortToken(v))
                        : (bytes(v).length <= 12 ? hexb(v) : '⋯' + bytes(v).length + 'B');
        el.title = `#${i} · ${JSON.stringify(v)}${t.special ? ' ← 特殊 token ' + t.special : ''}`;
        el.onmouseenter = el.onclick = () => {
          const shown = v.length > 3000 ? v.slice(0, 3000) + '…（共 ' + v.length + ' 字符，已截断显示）' : v;
          document.getElementById('tkDetail3').textContent =
            [`位置        : #${i} / ${L.n}`,
             `token 文本  : ${JSON.stringify(shown)}`,
             `logprob     : ${t.logprob}`,
             `UTF-8 字节  : ${bytes(v).length} 字节  ${hexb(v)}`,
             `可打印 ASCII: ${printable(v) ? '是' : '否'}`,
             `归属字段    : ${t.field === 'reasoning' ? '思考（reasoning_content）' : t.field === 'content' ? '正文（content）' : '★ 边界 token（被服务端消费，两个字段里都没有）'}`,
             t.special ? `★ 文本对应已知特殊 token：${t.special}（单独一个 logprob 条目 ⇒ 是单 token）`
                       : (isGone ? '★ 该 token 出现在生成里但不在交付文本中（被丢弃）'
                                 : '（普通文本 token；若标记类文字被拆成多个条目 ⇒ 是文本而非特殊 token）')].join('\n');
          [...cont.children].forEach(x => x.classList.remove('sel')); el.classList.add('sel');
        };
        cont.append(el);
      });
      wireMarks(cont, L.tokens, c.querySelector('#mkBarLp'), c.querySelector('#mkSum'));
      // 末尾标记：自然结束 / 被截断
      const endEl = document.createElement('span');
      endEl.className = 'tk endmark';
      endEl.style.cssText = 'flex-basis:100%;text-align:left;margin-top:4px;padding:3px 8px;' +
        (run.meta.truncated ? 'background:#ff7b7222;border-color:#ff7b72;color:#ffb3ad'
                            : 'background:#7ee78722;border-color:#7ee787;color:#b9f6c8');
      endEl.textContent = run.meta.truncated
        ? `⏸ 文本到此被截断：达到输出预算 ${run.meta.completion_tokens}/${run.meta.max_tokens} tokens（末尾不完整与此有关，与标记切断无关）`
        : `⏹ 自然结束（${run.meta.finish}）：这是模型的完整输出`;
      cont.append(endEl);
      // 特殊标记的 token 数统计
      const mk = L.tokens.map((t, i) => ({ ...t, i })).filter(t => /<\/?think>/.test(t.text || ''));
      if (mk.length) cont.insertAdjacentHTML('afterend',
        `<div class="hint">出现 &lt;think&gt;/&lt;/think&gt; 字样的 token：${mk.map(t => `#${t.i} ${JSON.stringify(t.text)}${t.special ? '（单 token = 特殊 token ' + t.special + '）' : '（文本）'}`).join('；')}</div>`);
    }
  } else if (view === 'tok') {
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = `<h2>输出 token 网格 <small>${t.out.n} 个 token · 悬停看 id / 字节 / 是否特殊</small></h2>
      <div class="bar" id="mkBarOut"></div>
      <div class="mksum" id="mkSumOut"></div>
      <div id="gridOut"></div>
      <div class="legend">
        <span><i class="sw" style="background:#ff7b7255;border-color:#ff7b72"></i>think 标记（154841/154842）</span>
        <span><i class="sw" style="background:#ff7b7233;border-color:#ff7b72"></i>其它特殊 token</span>
        <span><i class="sw" style="background:#c9a7ff22;border-color:#c9a7ff66"></i>&lt;|…|&gt; 文本</span>
        <span><i class="sw" style="background:#12161d"></i>普通 token</span>
        <span><i class="sw" style="background:#12161d;color:#79c0ff"></i>纯字节</span>
      </div>
      <div class="hint">每个格子是一个 token（不是字符）。右上统计：特殊 token ${t.out.tokens.filter(x=>x.special).length} / ${t.out.n}</div>
      <h2 style="margin-top:12px">选中 token 详情</h2><div id="tkDetail">把鼠标放到任意格子上…</div>`;
    pane.append(c);
    tokenGrid(c.querySelector('#gridOut'), t.out.tokens, t.out.n, c.querySelector('#tkDetail'), '输出');
    wireMarks(c.querySelector('#gridOut'), t.out.tokens, c.querySelector('#mkBarOut'), c.querySelector('#mkSumOut'));
  } else if (view === 'tokin') {
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = `<h2>输入 token 网格 <small>prompt 被切成什么（${t.input.n} 个）</small></h2>
      <div id="gridIn"></div>
      <div class="hint">prompt 里写的 <code>&lt;think&gt;</code>/<code>&lt;/think&gt;</code> 在**输入侧**就已经是特殊 token 了
        （红色格子）—— 模型看到的本来就是控制符。</div>
      <div class="bar" id="mkBarIn"></div>
      <div class="mksum" id="mkSumIn"></div>
      <h2 style="margin-top:12px">选中 token 详情</h2><div id="tkDetail2">把鼠标放到任意格子上…</div>`;
    pane.append(c);
    tokenGrid(c.querySelector('#gridIn'), t.input.tokens, t.input.n, c.querySelector('#tkDetail2'), '输入');
    wireMarks(c.querySelector('#gridIn'), t.input.tokens, c.querySelector('#mkBarIn'), c.querySelector('#mkSumIn'));
  } else if (view === 'prefill') {
    const pf = it.prefill, V = pf.verification, S = pf.stats;
    const srcCls = { 'req': (t) => t.detail.startsWith('msg') ? 'pf-msg' : 'pf-tool', 'tpl': () => 'pf-tpl' };
    const srcName = (t) => t.src === 'tpl' ? 'chat template 注入' : (t.detail.startsWith('msg') ? '请求内容·消息 ' + t.detail.split(':')[0] : '请求内容·工具');
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = `<h2>输入（prefill）token 溯源 <small>${S.n} 个 token · ${S.chars} 字符</small></h2>
      <div class="evbox ${V.match ? 't-ok' : 't-bad'}">
        <b>${V.match ? '✅ 与端点对齐' : '❌ 未对齐'}：渲染 ${V.rendered_tokens} token == 端点 <code>usage.prompt_tokens</code> ${V.endpoint_prompt_tokens}</b>
        <ul>
          <li>模板来源：<code>${esc(V.template)}</code>（<code>${esc(V.tokenizer)}</code>，没有 --chat-template 覆盖）</li>
          <li>逐 token 文本可拼回渲染串（<code>concat_ok=${V.concat_ok}</code>）⇒ 字符区间归因精确</li>
          <li>本次模板参数：<code>${esc(JSON.stringify(V.kwargs))}</code>${V.kwargs && V.kwargs.reasoning_effort ? '（<b>请求里的 reasoning_effort 会被转发进模板</b>）' : ''}</li>
        </ul>
      </div>
      <div class="pgrid" id="pfGrid"></div>
      <div class="hint" id="pfDetail">把鼠标放到任意格子上…</div>
      <div class="bar" id="pfBar"></div>
      <div class="legend">
        <span><i class="sw" style="background:#7ee78722;border-color:#7ee787"></i>请求内容 · 消息（user/assistant/tool 正文与思考）</span>
        <span><i class="sw" style="background:#58a6ff22;border-color:#58a6ff"></i>请求内容 · 工具定义 / 工具调用参数</span>
        <span><i class="sw" style="background:#c9a7ff22;border-color:#c9a7ff66"></i>chat template 注入</span>
        <span><i class="sw" style="border-color:#ff7b72;box-shadow:inset 0 0 0 2px #ff7b72"></i>special token（tokenizer special=true，<code>skip_special_tokens</code> 会清）</span>
        <span><i class="sw" style="border-color:#f2cc60;box-shadow:inset 0 0 0 2px #f2cc60"></i>added token（special=false，<b>不会被清</b>，如 <code>&lt;think&gt;</code>）</span>
      </div>
      <h2 style="margin-top:12px">分类统计</h2>
      <table>
        <tr><th>请求内容 · 消息</th><td class="m">${S.by_source.req_msg} token</td>
            <th>请求内容 · 工具定义/调用</th><td class="m">${S.by_source.req_tools} token</td></tr>
        <tr><th>chat template 注入</th><td class="m">${S.by_source.tpl} token</td>
            <th>其中 special token</th><td class="m">${S.n_special}（会被 skip 清掉 ${S.n_special_skipable}）</td></tr>
      </table>
      ${S.n_special_in_req ? `<div class="evbox t-bad" id="reqspWarn"><b>⚠️ 请求内容里有 ${S.n_special_in_req} 个真正的 special token</b>
        <ul><li>${Object.entries(S.special_in_req).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<code>${esc(k)}</code>×${v}`).join('　')}</li>
        <li>出处：${Object.entries(S.special_in_req_where).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([k,v])=>`<code>${esc(k)}</code>×${v}`).join('　')}</li>
        <li>它们<b>不是普通文本</b>：tokenizer 在渲染后的整串上按 added-token 表优先匹配，所以历史消息里写 <code>&lt;/think&gt;</code>（或 <code>&lt;|user|&gt;</code> 这种角色标记）会以<b>控制符</b>身份进入输入。实测：端点 <code>usage.prompt_tokens</code> 与「特殊标记优先匹配」完全一致。</li></ul></div>` : ''}
      <div class="hint">模板注入明细：${Object.entries(S.tpl_kinds).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<code>${esc(k)}</code> ${v}`).join('　')}</div>
      <div class="hint">出现的 special token：${Object.entries(S.special_by_text).sort((a,b)=>b[1]-a[1]).slice(0,12).map(([k,v])=>`<code>${esc(k)}</code>×${v}`).join('　')||'（无）'}</div>
      <details><summary>请求侧构成（消息 / 工具）</summary>
        <div class="hint">消息：${pf.messages.map(m=>`#${m.i} <code>${esc(m.role)}</code> 正文 ${m.chars} 字${m.reasoning_chars?` · 思考 ${m.reasoning_chars} 字`:''}${m.tool_calls?` · 工具调用 ${m.tool_calls}`:''}`).join('<br>')}</div>
        <div class="hint">工具定义（${pf.tools.length} 个）：${pf.tools.map(t=>`<code>${esc(t.name)}</code> ${t.chars} 字`).join('　')}</div>
      </details>`;
    pane.append(c);
    const grid = c.querySelector('#pfGrid'), det = c.querySelector('#pfDetail'), bar = c.querySelector('#pfBar');
    const STA = pf.tokens.st, NP = pf.prompt.length;
    pf.tokens.ids.forEach((tid, i) => {
      const tx = pf.tokens.texts[i], [src, detail] = pf.tokens.labels[pf.tokens.lab[i]].split('|');
      const spKind = pf.tokens.sp[i];
      const t = { id: tid, text: tx, src: src, detail: detail,
                  s: STA[i], e: (i + 1 < STA.length ? STA[i + 1] : NP),
                  special: spKind > 0, skip: spKind === 2 };
      const el = document.createElement('span');
      el.className = 'tk pf ' + srcCls[t.src](t) + (t.special ? (t.skip ? ' pf-sp-skip' : ' pf-sp-added') : '')
        + (t.special && t.src === 'req' ? ' pf-req-sp' : '');
      el.textContent = t.text;
      el.dataset.i = i; el.dataset.src = t.src; el.dataset.sp = t.special ? '1' : '0';
      el.onclick = () => {
        [...grid.children].forEach(x => x.classList.remove('sel')); el.classList.add('sel');
        det.innerHTML = [`token #${i}`, `id          : ${t.id}`, `文本        : ${JSON.stringify(t.text)}`,
          `来源        : ${srcName(t)}${t.detail.startsWith('msg') ? '（' + t.detail.split(':').slice(1).join(':') + '）' : ''}`,
          `字符区间    : [${t.s}, ${t.e})`,
          t.special ? `特殊标记    : ${SPECIAL_ID2TXT[t.id] || '（added token）'}${t.skip ? '（tokenizer special=true ⇒ skip_special_tokens 会清掉）' : '（special=false ⇒ skip 不会清）'}` : '（普通文本 token）'
        ].join('\n');
      };
      grid.append(el);
    });
    const setFilter = (mode) => {
      grid.dataset.filter = mode;
      [...bar.children].forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
    };
    [['all','全部'],['req','只看请求内容'],['tpl','只看模板注入'],['sp','只看 special token'],
     ['reqsp','只看 请求内容里的 special token']].forEach(([mode,label]) => {
      const b = document.createElement('button'); b.textContent = label; b.dataset.mode = mode;
      b.onclick = () => setFilter(mode); bar.append(b);
    });
    setFilter('all');
  } else if (view === 'verdict') {
    const v = t.verdict;
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = `<h2>判定：输出里的 <code>&lt;/think&gt;</code> 是 special token 还是文本？</h2>
      <table>
        <tr><th>形态</th><th>次数</th><th>服务端行为</th></tr>
        <tr><td class="m">special token id 154842（<code>&lt;/think&gt;</code>）</td>
            <td class="m bad">${v.special_close}</td><td class="m bad">按控制信号：切字段 + 消费掉（字段里看不到它）</td></tr>
        <tr><td class="m">special token id 154841（<code>&lt;think&gt;</code>）</td>
            <td class="m bad">${v.special_open}</td><td class="m bad">同上</td></tr>
        <tr><td class="m">普通文本 <code>&lt;/think&gt;</code>（text 字段里）</td>
            <td class="m ok">${v.literal_close}</td><td class="m ok">当字符处理：保留、不切字段</td></tr>
        <tr><td class="m">普通文本 <code>&lt;think&gt;</code>（text 字段里）</td>
            <td class="m ok">${v.literal_open}</td><td class="m ok">同上</td></tr>
      </table>
      <div class="hint">同一个可见字符串，<b>是哪一种形态决定了服务端行为</b>；只有 token id 能分辨。</div>
      <h2 style="margin-top:12px">交付时被丢掉的 token <small>所有 token 文本拼接 vs 服务端 text 字段</small></h2>
      <pre>${t.drops.length ? t.drops.map(d => `<mark class="del">${esc(d.text)}</mark>  ← ${esc(d.kind)}`).join('\n')
                        : '<span class="k">（无差异）</span>'}</pre>
      <h2 style="margin-top:12px">text 字段（skip_special_tokens=false / true）</h2>
      <pre>${window.__hl(t.out.text)}</pre>
      <pre style="margin-top:8px">${window.__hl(t.dropText)}</pre>`;
    pane.append(c);
  } else if (view === 'seam') {
    if (run) {
      const m = run.meta;
      const c = document.createElement('div'); c.className = 'card';
      c.innerHTML = `<h2>接缝现场 <small>chat 端点交付的两个字段</small></h2>
        <div class="cat">${m.concat ? window.__hl(m.concat) : '（content 为空）'}</div>
        <div class="seamview" style="margin-top:12px">
          <div><div class="k">← reasoning_content 尾部 300 字</div><pre>${window.__hl(m.reasoning_tail)}</pre></div>
          <div><div class="k">content 开头 300 字 →</div><pre>${window.__hl(m.content_head)}</pre></div>
        </div>
        <div class="hint">结束方式：${endBadge(m)}${m.truncated ? '（末尾不完整来自输出预算，与标记切断无关）' : ''}</div>
        <div class="hint">客观标记：reasoning 里 &lt;think&gt;//&lt;/think&gt; ${m.flags.markers_in_reasoning['<think>']}/${m.flags.markers_in_reasoning['</think>']}
          · content 里 ${m.flags.markers_in_content['<think>']}/${m.flags.markers_in_content['</think>']}
          · reasoning 反引号奇数个：${m.flags.reasoning_unclosed_backtick ? '是' : '否'}</div>`;
      pane.append(c);
    }
    if (t) {
      const c = document.createElement('div'); c.className = 'card';
      c.innerHTML = `<h2>token 侧的字段边界 <small>chat 一次生成的 reasoning/content（与上面的 token 不是同一次）</small></h2>
        <div class="seamview">
          <div><div class="k">reasoning_content 尾部 300</div><pre>${esc(t.chat.reasoning.slice(-300))}</pre></div>
          <div><div class="k">content 开头 300</div><pre>${esc(t.chat.content.slice(0, 300))}</pre></div>
        </div>
        <div class="hint">把两个字段直接拼起来读一遍，看边界是不是落在句子中间 —— 这正是「表达器/解析器把标记当控制符」的结果。</div>`;
      pane.append(c);
    }
  } else {
    pane.append(paneCard('请求头（凭据已遮挡）', run.reqHeaders));
    pane.append(paneCard('响应头', run.resHeaders));
  }
}
refreshList();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
