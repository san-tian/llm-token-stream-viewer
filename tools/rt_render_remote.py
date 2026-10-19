import json, sys
from transformers import AutoTokenizer
import os
MODEL_PATH = os.environ.get('MODEL_PATH', '/path/to/model')
tk = AutoTokenizer.from_pretrained(MODEL_PATH)
d = json.load(sys.stdin)
kw = d.get('kwargs') or {}
pp = tk.apply_chat_template(d['messages'], tools=d.get('tools'), tokenize=False, add_generation_prompt=True, **kw)
r  = tk.apply_chat_template(d['messages'], tools=d.get('tools'), tokenize=True,  add_generation_prompt=True, **kw)
if hasattr(r, 'keys') and 'input_ids' in r.keys(): r = r['input_ids']
ids = r.tolist() if hasattr(r, 'tolist') else list(r)
if ids and isinstance(ids[0], list): ids = ids[0]
enc = tk(pp, add_special_tokens=False, return_offsets_mapping=True)
offs = [[int(a), int(b)] for a, b in enc['offset_mapping']]
same = list(enc['input_ids']) == list(ids)
tok_text = [tk.decode([i], skip_special_tokens=False, errors='replace') for i in ids]
out = {'prompt': pp, 'ids': ids, 'offsets': offs, 'ids_retok_match': bool(same),
       'offsets_n': len(offs), 'tok_text': tok_text, 'concat_ok': ''.join(tok_text) == pp, 'kwargs': kw,
       'id_len': len(ids)}
try:
    out['special'] = {str(t): {'text': str(x), 'special': bool(getattr(x, 'special', False))}
                      for t, x in tk.added_tokens_decoder.items()}
except Exception:
    out['special'] = {}
print(json.dumps(out, ensure_ascii=False))
