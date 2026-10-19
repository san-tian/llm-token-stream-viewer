#!/usr/bin/env python3
"""判定「生成出来的 token 能不能被前缀缓存复用」——决定 retokenization 漂移有没有缓存代价。
判据：
  g2.cached ≈ 全序列（页对齐） → 生成 token 可复用 ⇒ 漂移点之后全部要重算（漂移有缓存代价）
  g2.cached ≈ 仅 prompt（页对齐）→ 生成 token 不可复用 ⇒ 漂移发生在"从未进缓存"的区域（无代价）
用法: PROBE_KEY=... python3 probe_cache_reuse_modes.py
"""
import json, os, urllib.request
BASE = os.environ.get("TOKEN_BASE", os.environ.get("TOKEN_BASE", "http://127.0.0.1:30000")); KEY = os.environ["PROBE_KEY"]
MODEL = os.environ.get("PROBE_MODEL", "glm52-fp8-official")
def post(p, pl, t=600):
    r = urllib.request.Request(BASE + p, data=json.dumps(pl).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as x: return json.loads(x.read())
def gen(text, n=1):
    r = post("/generate", {"text": text, "sampling_params": {"max_new_tokens": n, "temperature": 0,
                                                            "skip_special_tokens": True}})
    mi = r.get("meta_info") or {}
    return {"text": r.get("text") or "", "pt": mi.get("prompt_tokens"),
            "cached": mi.get("cached_tokens"), "ids": r.get("output_ids") or []}
if __name__ == "__main__":
    P = "以下是背景材料。" + ("海豚是哺乳动物，用肺呼吸，靠回声定位捕食，群体协作围猎。 " * 80) \
        + "\n\n请写一段 150 字左右的分析，不要写标记。"
    g1 = gen(P, 200)
    g2 = gen(P + g1["text"], 1)          # 输出原样拼回（token 序列逐位相同，不经模板重渲染）
    g3 = gen(P + g1["text"], 1)          # 再发一次：此时它已是 prompt token
    full = g1["pt"] + len(g1["ids"])
    print(f"① 首轮          prompt={g1['pt']} 生成={len(g1['ids'])} cached={g1['cached']}")
    print(f"② 输出原样拼回  prompt={g2['pt']} cached={g2['cached']}"
          f"   （可复用应为 {(full//64)*64}；仅 prompt 应为 {(g1['pt']//64)*64}）")
    print(f"③ 同 prompt 再发 cached={g3['cached']}  （应 ≈ {(g2['pt']//64)*64}，说明缓存本身工作正常）")
    reuse = (g2["cached"] or 0) > (g1["pt"] // 64) * 64
    print(f"\n判定：生成 token {'可复用 ⇒ 漂移有缓存代价' if reuse else '不可复用 ⇒ 漂移无缓存代价（本机实测）'}")
    print("备注：本机是 PD 分离的 prefill 实例（1021 号口 = --disaggregation-mode prefill，"
          "生成发生在 decode 实例 1022），prefill 侧前缀缓存天然只含 prompt token。")
