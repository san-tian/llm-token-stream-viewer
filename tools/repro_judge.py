#!/usr/bin/env python3
"""判定某次响应是否「复现了字段切断 且 带 token 序列（logprobs）」——纯函数，无外部依赖。"""
import difflib
import json

SENT_END = ".。!?！？"


def judge(body_path):
    """返回 (是否合格, 摘要)。全部基于同一次响应的可复核计算。"""
    try:
        j = json.loads(body_path.read_text())
        ch = j["choices"][0]
        m = ch.get("message") or {}
        R = m.get("reasoning_content") or ""
        C = m.get("content") or ""
        toks = [e["token"] for e in ((ch.get("logprobs") or {}).get("content") or [])]
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"{type(e).__name__}: {e}"}
    if not toks:
        return False, {"error": "无 logprobs"}
    joined = "".join(toks)
    finish = (j["choices"][0].get("finish_reason") or "")
    truncated = finish == "length"          # 截断不是"切断"，排除
    k = None
    for strip in (lambda x: x, lambda x: x.rstrip(), lambda x: x.strip()):
        acc = ""
        for i, t in enumerate(toks):
            acc += t
            if strip(acc) == strip(R):
                k = i + 1
                break
        if k is not None:
            break
    tail = R.rstrip()
    ends_sentence = bool(tail) and tail[-1] in SENT_END
    unbal_bt = R.count("`") % 2 == 1
    unbal_dq = R.count('"') % 2 == 1
    marker_at_boundary = False
    if k is not None:
        marker_at_boundary = "".join(toks[k:k + 3]).startswith("</think>") or (k < len(toks) and toks[k] == "</think>")
    import difflib
    dropped = []
    if joined != R + C:
        sm = difflib.SequenceMatcher(None, joined, R + C, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace") and i2 > i1:
                dropped.append(joined[i1:i2])
    marker_consumed = any("</think>" in d for d in dropped)
    visible_cut = (not ends_sentence) or unbal_bt or unbal_dq
    # finish=length 只作提示：切断发生在标记处（marker_at_boundary + consumed），与后面的截断无关
    ok = (k is not None) and marker_at_boundary and marker_consumed and visible_cut
    why = []
    if truncated:
        why.append("被 max_tokens 截断（不算切断）")
    if k is None:
        why.append("未对齐")
    if not marker_at_boundary:
        why.append("边界不是 </think>")
    if not marker_consumed:
        why.append("标记未被消费")
    if not visible_cut:
        why.append("边界处没有可见切断")
    return ok, {"finish": finish, "truncated": truncated, "boundary": k, "boundary_token": (toks[k] if k is not None and k < len(toks) else None),
                "n_tokens": len(toks), "reasoning_chars": len(R), "content_chars": len(C),
                "not_sentence_end": not ends_sentence, "unbalanced_backtick": unbal_bt,
                "unbalanced_dquote": unbal_dq, "marker_at_boundary": marker_at_boundary,
                "marker_consumed": marker_consumed, "dropped": dropped[:4],
                "reasoning_tail": R[-120:], "content_head": C[:120], "why_not": why}


