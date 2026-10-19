#!/usr/bin/env python3
"""输入（prefill）token 溯源：每个 token 是「请求内容」还是「chat template 注入」，
以及它本身是 special token 还是普通文本。
数据来源：在模型所在机器上用该模型的 chat_template.jinja + tokenizer 渲染（prefill/*.json）。
  MODEL_PATH 环境变量指向模型目录（默认 /path/to/model）。"""
import json, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "prefill_attr"
ROLE_MARK = {"system": "<|system|>", "user": "<|user|>", "assistant": "<|assistant|>",
             "tool": "<|observation|>", "observation": "<|observation|>"}


def add(spans, s, e, kind, detail=""):
    if e > s:
        spans.append({"s": s, "e": e, "kind": kind, "detail": detail})


def build_spans(prompt, msgs, tools, kwargs):
    spans, cursor = [], 0
    # ① 模板固定前缀 + 推理力度行
    add(spans, 0, 0, "tpl", "prefix")   # 占位，稍后统一按字面定位
    # ② 消息（请求内容）
    for i, m in enumerate(msgs):
        role = m.get("role")
        for field in ("reasoning_content", "content"):
            v = m.get(field)
            if not isinstance(v, str) or not v.strip():
                continue
            for probe in (v, v.strip()):
                s = prompt.find(probe, cursor)
                if s >= 0:
                    add(spans, s, s + len(probe), "req", f"msg{i}:{role}:{field}")
                    cursor = s + len(probe)
                    break
        # 工具调用（名字 + 参数值都是请求里的数据）
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            name = fn.get("name")
            if name:
                s = prompt.find("<tool_call>" + str(name), cursor)
                if s >= 0:
                    add(spans, s + len("<tool_call>"), s + len("<tool_call>") + len(str(name)), "req", f"toolcall:m{i}:name")
                    cursor = s + len("<tool_call>")
            args = fn.get("arguments")
            if isinstance(args, dict):
                for k, v in args.items():
                    rendered = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                    s = prompt.find("<arg_value>" + str(rendered) + "</arg_value>", cursor)
                    if s >= 0:
                        add(spans, s + len("<arg_value>"), s + len("<arg_value>") + len(str(rendered)), "req", f"toolcall:m{i}:arg")
                        cursor = s + len("<arg_value>")
    # ③ 工具定义：真正的 <tools>\n{json…}\n</tools>（注意别抓到 preamble 散文里那句 “within <tools></tools> XML tags”）
    import re as _re
    mb = _re.search(r"<tools>\n(.*?)\n</tools>", prompt, _re.S)
    tools_block = mb.span(1) if mb else None
    if mb:
        add(spans, mb.start(1), mb.end(1), "req", "tools:definitions")
    req_spans = [x for x in spans if x["kind"] == "req"]
    # ④ 模板注入：按字面标注已知片段（不得与请求内容重叠）
    tpl_spans = []
    def add_tpl(lit, kind):
        if not lit:
            return
        start = 0
        while True:
            i = prompt.find(lit, start)
            if i < 0:
                break
            add(tpl_spans, i, i + len(lit), "tpl", kind)
            start = i + 1
    add_tpl("[gMASK]<sop>", "prefix")
    add_tpl(f"<|system|>Reasoning Effort: {str(kwargs.get('reasoning_effort', 'max')).capitalize()}", "effort")
    for mark in set(ROLE_MARK.values()):
        add_tpl(mark, "roles")
    for k in ("<think>", "</think>"):
        add_tpl(k, "think")
    for k in ("<tool_call>", "</tool_call>", "<arg_key>", "</arg_key>", "<arg_value>", "</arg_value>"):
        add_tpl(k, "toolcall")
    for k in ("<tool_response>", "</tool_response>"):
        add_tpl(k, "toolresp")
    ps = prompt.find("# Tools")
    if ps >= 0:
        pb = tools_block[0] if tools_block else prompt.find("</tools>")
        add_tpl(prompt[ps:pb], "tools-preamble")
        te_end = prompt.find("</tools>", pb) + len("</tools>") if prompt.find("</tools>", pb) >= 0 else pb
        # 尾段到「下一个角色标记」为止（不能用 <|assistant|>：那会把后面的消息全吞进来）
        nxts = [prompt.find(mk, te_end) for mk in set(ROLE_MARK.values())]
        nxts = [x for x in nxts if x > te_end]
        nxt = min(nxts) if nxts else len(prompt)
        add_tpl(prompt[te_end:nxt], "tools-preamble")
    # 请求内容优先：模板片段去掉与请求内容重叠的部分
    def overlaps(a, b):
        return a["s"] < b["e"] and b["s"] < a["e"]
    tpl_spans = [x for x in tpl_spans if not any(overlaps(x, r) for r in req_spans)]
    spans = req_spans + tpl_spans
    return sorted(spans, key=lambda x: x["s"])


def classify_tokens(tok_text, spans, ids, special, offsets=None):
    """offsets = tokenizer 给的精确字符区间（多字节字符被切开时也正确）；
    没有则退化为「按拼接长度累加」。"""
    pos, data = 0, []
    for i, (tx, tid) in enumerate(zip(tok_text, ids)):
        if offsets and i < len(offsets):
            s, e = offsets[i]
        else:
            s, e = pos, pos + len(tx)
        pos = e
        hit = None
        for sp in spans:                                  # ① 完整包含
            if sp["s"] <= s and e <= sp["e"] and (e > s or sp["e"] > sp["s"]):
                hit = sp
                break
        if hit is None:                                   # ② 空区间/跨界：按起点所在 span
            for sp in spans:
                if sp["s"] <= s < sp["e"]:
                    hit = sp
                    break
        if hit is None:                                   # ③ 按重叠量
            for sp in spans:
                if min(e, sp["e"]) - max(s, sp["s"]) > 0:
                    hit = sp
                    break
        src, detail = ("req", hit["detail"]) if (hit and hit["kind"] == "req") else ("tpl", (hit or {}).get("detail", "other"))
        sp_info = special.get(str(tid))
        data.append({"s": s, "e": e, "id": tid, "text": tx, "src": src, "detail": detail,
                     "special": bool(sp_info), "special_skipable": bool(sp_info and sp_info.get("special"))})
    return data


def main():
    OUT.mkdir(exist_ok=True)
    summary = []
    for p in sorted(ROOT.glob("prefill/*.json")):
        d = json.loads(p.read_text())
        cid = d["case_id"]
        spans = build_spans(d["prompt"], d["messages_norm"], d.get("tools"), d.get("kwargs") or {})
        toks = classify_tokens(d["tok_text"], spans, d["ids"], d.get("special") or {}, d.get("offsets"))
        n = len(toks)
        by = {"req_msg": 0, "req_tools": 0, "tpl": 0}
        tpl_kind, specials = {}, {}
        for t in toks:
            if t["src"] == "req":
                by["req_msg" if t["detail"].startswith("msg") else "req_tools"] += 1
            else:
                by["tpl"] += 1
                tpl_kind[t["detail"]] = tpl_kind.get(t["detail"], 0) + 1
            if t["special"]:
                specials[t["text"]] = specials.get(t["text"], 0) + 1
        # 请求内容里 vs 模板注入里的 special token（分文本与出处统计）
        sp_req, sp_tpl, sp_req_where = {}, {}, {}
        for t in toks:
            if not t["special"]:
                continue
            bucket = sp_req if t["src"] == "req" else sp_tpl
            bucket[t["text"]] = bucket.get(t["text"], 0) + 1
            if t["src"] == "req":
                sp_req_where[t["detail"]] = sp_req_where.get(t["detail"], 0) + 1
        obj = {"case_id": cid, "run": d["run"], "prompt": d["prompt"], "ids": d["ids"],
               "tokens": toks, "special_map": d.get("special"),
               "verification": {"rendered_tokens": n, "endpoint_prompt_tokens": d.get("endpoint_prompt_tokens"),
                                "match": n == d.get("endpoint_prompt_tokens"), "concat_ok": d.get("concat_ok"),
                                "offsets": bool(d.get("offsets")), "ids_retok_match": d.get("ids_retok_match"),
                                "template": "chat_template.jinja (md5 e4c9e1000a513f680dd796f76d1097e0)",
                                "tokenizer": os.environ.get("MODEL_PATH", "/path/to/model"), "kwargs": d.get("kwargs")},
               "stats": {"n": n, "chars": len(d["prompt"]), "by_source": by, "tpl_kinds": tpl_kind,
                         "special_by_text": specials, "n_special": sum(specials.values()),
                         "n_special_skipable": sum(1 for t in toks if t["special_skipable"]),
                         "n_special_in_req": sum(sp_req.values()), "special_in_req": sp_req,
                         "n_special_in_tpl": sum(sp_tpl.values()), "special_in_tpl": sp_tpl,
                         "special_in_req_where": sp_req_where},
               "messages": [{"i": i, "role": m.get("role"), "chars": len(m.get("content") or ""),
                             "reasoning_chars": len(m.get("reasoning_content") or ""),
                             "tool_calls": len(m.get("tool_calls") or [])} for i, m in enumerate(d["messages_norm"])],
               "tools": [{"name": ((t.get("function") or t).get("name")), "chars": len(json.dumps(t, ensure_ascii=False))}
                         for t in (d.get("tools") or [])]}
        (OUT / f"{cid}.json").write_text(json.dumps(obj, ensure_ascii=False))
        s = obj["stats"]
        summary.append((cid, n, s["by_source"], s["n_special"], s["n_special_skipable"]))
        print(f"\n=== {cid}（{d['run']}）{n} token / {len(d['prompt'])} 字 ===")
        print(f"  请求内容: 消息 {s['by_source']['req_msg']} · 工具定义/调用 {s['by_source']['req_tools']}")
        print(f"  模板注入: {s['by_source']['tpl']}（{', '.join(f'{k} {v}' for k, v in sorted(s['tpl_kinds'].items(), key=lambda x: -x[1]))}）")
        print(f"  special token: {s['n_special']}（其中 skip_special_tokens 会清的 {s['n_special_skipable']}）")
        print(f"  特殊词表命中: {dict(sorted(s['special_by_text'].items(), key=lambda x: -x[1])[:8])}")
    print("\n\n=== 汇总 ===")
    print(f"{'case':24s} {'tokens':>7s} {'请求-消息':>8s} {'请求-工具':>8s} {'模板':>7s} {'special':>8s}")
    for cid, n, by, ns, nsk in summary:
        print(f"{cid:24s} {n:>7d} {by['req_msg']:>8d} {by['req_tools']:>8d} {by['tpl']:>7d} {ns:>8d}")


if __name__ == "__main__":
    main()
