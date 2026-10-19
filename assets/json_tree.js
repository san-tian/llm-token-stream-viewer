/* ---------- 可折叠 JSON 树 + 非常见字符可见化（无外部依赖）----------
   用法：el.append(jsonTree(parsedValue, {preferReasoning:true, vis:true, focus:[...]})) */
(function (global) {
  const typeOf = v => v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v;
  const escDefault = x => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const H = () => global.__hl || escDefault;
  const DEFAULT_PRIORITY = ['role', 'reasoning_content', 'content', 'tool_calls'];
  const KEYCOLOR = { reasoning_content: 'k-reason', content: 'k-content' };

  /* ================= 非常见字符识别 ================= */
  const NAMES = {
    0x00: 'NUL', 0x01: 'SOH', 0x02: 'STX', 0x03: 'ETX', 0x04: 'EOT', 0x05: 'ENQ', 0x06: 'ACK',
    0x07: 'BEL', 0x08: 'BS', 0x09: 'TAB', 0x0b: 'VT', 0x0c: 'FF', 0x0e: 'SO', 0x0f: 'SI',
    0x10: 'DLE', 0x11: 'DC1', 0x12: 'DC2', 0x13: 'DC3', 0x14: 'DC4', 0x15: 'NAK', 0x16: 'SYN',
    0x17: 'ETB', 0x18: 'CAN', 0x19: 'EM', 0x1a: 'SUB', 0x1b: 'ESC', 0x1c: 'FS', 0x1d: 'GS',
    0x1e: 'RS', 0x1f: 'US', 0x7f: 'DEL',
    0x85: 'NEL', 0xad: 'SHY 软连字符',
    0x200b: 'ZWSP 零宽空格', 0x200c: 'ZWNJ 零宽不连字', 0x200d: 'ZWJ 零宽连字',
    0x200e: 'LRM 左到右标记', 0x200f: 'RLM 右到左标记',
    0x2028: 'LS 行分隔', 0x2029: 'PS 段分隔',
    0x202a: 'LRE', 0x202b: 'RLE', 0x202c: 'PDF', 0x202d: 'LRO', 0x202e: 'RLO',
    0x2060: 'WJ 词连接符', 0x2061: '函数应用', 0x2062: '不可见乘号', 0x2063: '不可见分隔符',
    0x2066: 'LRI', 0x2067: 'RLI', 0x2068: 'FSI', 0x2069: 'PDI',
    0xfeff: 'BOM 零宽不断行', 0xfffd: 'U+FFFD 替换字符',
  };
  function classify(cp) {
    if (cp === 0x0a || cp === 0x0d) return null;      // 换行：作为正常文本保留（页面靠 pre-wrap 显示）
    if (NAMES[cp]) return { name: NAMES[cp] };
    if (cp <= 0x1f) return { name: 'C0 控制' };        // 0x00–0x1f 其余（含 NUL/BEL/BS/ESC）——都不能原样进 DOM
    if (cp === 0x7f) return { name: 'DEL' };
    if (cp >= 0x80 && cp <= 0x9f) return { name: 'C1 控制' };
    if (cp >= 0x2000 && cp <= 0x200a) return { name: '固定宽度空白' };
    if (cp >= 0x202a && cp <= 0x202e) return { name: '双向控制' };
    if (cp >= 0x2060 && cp <= 0x2064) return { name: '不可见连接/运算符' };
    if (cp >= 0x2066 && cp <= 0x2069) return { name: '双向隔离' };
    if (cp >= 0xfe00 && cp <= 0xfe0f) return { name: '变体选择符 VS' };
    if (cp >= 0xe000 && cp <= 0xf8ff) return { name: '私用区 PUA' };
    if (cp >= 0xfdd0 && cp <= 0xfdef) return { name: '非字符' };
    if ((cp & 0xffff) === 0xfffe || (cp & 0xffff) === 0xffff) return { name: '非字符' };
    if (cp === 0x180e) return { name: '蒙古文元音分隔符' };
    return null;
  }
  const hex = s => [...new TextEncoder().encode(s)].map(b => b.toString(16).padStart(2, '0')).join(' ');
  const uPlus = cp => 'U+' + cp.toString(16).toUpperCase().padStart(4, '0');

  function chip(ch) {
    const cp = ch.codePointAt(0);
    const info = classify(cp) || { name: '?' };
    const b = hex(ch);
    return `<mark class="uchip" title="${escDefault(info.name)} · ${uPlus(cp)} · UTF-8 ${b} · 十进制 ${cp}">`
      + `<span class="uc-name">${escDefault(info.name)}</span>`
      + `<span class="uc-code">${uPlus(cp)}</span>`
      + `<span class="uc-bytes">${b}</span></mark>`;
  }

  /* 把一段字符串渲染成 HTML：非常见字符 → 可见标签；其余走 H()（保留标记高亮） */
  function renderString(text, vis) {
    let out = '', buf = '';
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      if (classify(cp)) {
        out += H()(buf); buf = '';
        // 关闭可见化时也不把控制字符塞进 DOM，改写成 \uXXXX 文本（原始字节请看「字节」dump 或 JSON 原文）
        out += vis ? chip(ch)
                   : `<span class="uesc" title="可见化已关闭；此处以 \\uXXXX 文本显示">\\u${cp.toString(16).toUpperCase().padStart(4, '0')}</span>`;
      } else buf += ch;
    }
    return out + H()(buf);
  }
  /* 渲染 JSON 文本：非常见字符可见化 + \uXXXX/\n 等转义序列高亮（正则先匹配标签，避免改坏属性） */
  function renderJsonText(text, vis) {
    let out = '', buf = '';
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      if (classify(cp)) {
        out += escDefault(buf); buf = '';
        out += vis ? chip(ch)
                   : `<span class="uesc" title="可见化已关闭；此处以 \\uXXXX 文本显示">\\u${cp.toString(16).toUpperCase().padStart(4, '0')}</span>`;
      } else buf += ch;
    }
    out += escDefault(buf);
    return out.replace(/(<[^>]*>)|(\\u[0-9a-fA-F]{4}|\\[ntr"\\])/g,
      (m, tag, esc) => tag ? tag : `<mark class="jesce">${esc}</mark>`);
  }

  function countUncommon(text) {
    let n = 0; const kinds = {};
    for (const ch of text) {
      const info = classify(ch.codePointAt(0));
      if (info) { n++; kinds[info.name] = (kinds[info.name] || 0) + 1; }
    }
    return { n, kinds };
  }

  /* ================= 路径复制 ================= */
  function copyPath(path, el) {
    const done = () => { el.classList.add('copied'); setTimeout(() => el.classList.remove('copied'), 700); };
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = path; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.append(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { window.prompt('路径（手动复制）', path); }
      ta.remove();
    };
    try {
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(path).then(done, fallback);
      else fallback();
    } catch (e) { fallback(); }
  }

  /* ================= 树 ================= */
  function orderedEntries(obj, priority, prefer) {
    const keys = Object.keys(obj);
    if (!prefer || !priority || !priority.length) return { entries: keys.map(k => [k, obj[k]]), changed: false };
    const first = priority.filter(k => keys.includes(k));
    const entries = [...first, ...keys.filter(k => !first.includes(k))].map(k => [k, obj[k]]);
    return { entries, changed: keys.some((k, i) => k !== entries[i][0]) };
  }

  function jsonTree(root, opts = {}) {
    const PREVIEW = opts.previewChars ?? 400;
    const OPEN_DEPTH = opts.openDepth ?? 2;
    const PRIORITY = opts.priority ?? DEFAULT_PRIORITY;
    const PREFER = opts.preferReasoning !== false;
    let VIS = opts.vis !== false;

    const wrap = document.createElement('div'); wrap.className = 'jsontree';
    const bar = document.createElement('div'); bar.className = 'jt-bar';
    const mk = (t, fn) => { const b = document.createElement('button'); b.textContent = t; b.onclick = fn; return b; };
    const stats = document.createElement('span'); stats.className = 'jt-stat';
    const filter = document.createElement('input'); filter.type = 'search'; filter.placeholder = '过滤键名…';
    const tree = document.createElement('div'); tree.className = 'jt-body';

    function setOpen(n, open) {
      n.classList.toggle('open', open);
      const tg = n.querySelector(':scope > .thead > .tgl');
      if (tg && n.classList.contains('composite')) tg.textContent = open ? '▾' : '▸';
    }
    function setOpenAll(pred) {
      wrap.querySelectorAll('.tnode').forEach(n => {
        const comp = n.classList.contains('composite');
        const open = comp && pred(+n.dataset.depth);
        n.classList.toggle('open', open);
        const tg = n.querySelector(':scope > .thead > .tgl');
        if (tg && comp) tg.textContent = open ? '▾' : '▸';
      });
    }
    const visChk = document.createElement('label'); visChk.className = 'jt-chk';
    const visBox = document.createElement('input'); visBox.type = 'checkbox'; visBox.checked = VIS;
    const visCount = document.createElement('span'); visCount.className = 'jt-viscount';
    visChk.append(visBox, document.createTextNode(' 可见化“不可见字符”（控制符 / 零宽 / BOM …）'), visCount);
    const legendBtn = document.createElement('button'); legendBtn.className = 'jt-help';
    legendBtn.textContent = 'ⓘ 颜色图例';
    const legendBox = document.createElement('div'); legendBox.className = 'jt-legendbox'; legendBox.style.display = 'none';

    const SAMPLE = 'a\u0007b\u200bc\ufeffd';
    function legendHTML() {
      return `<b>四套颜色互不相干，别混：</b>
        <table class="jt-lg">
          <tr><td>${vis() ? chip('\u0007') : escDefault('\\u0007')} 红标签</td>
              <td><b>不可见 / 非常见字符</b>（控制符、零宽、BOM、替换字符、私用区…）——
                  <b>只有这一类受左侧开关控制</b>：开 → 红色标签；关 → 显示成 <code>\\uXXXX</code> 文本</td></tr>
          <tr><td><mark class="tag">&lt;think&gt;</mark> 黄底</td>
              <td><b>字面 special token 文本</b>（<code>&lt;think&gt;</code>/<code>&lt;/think&gt;</code>/<code>&lt;|im_start|&gt;</code>…）
                  —— <b>始终高亮，与开关无关</b>（它只是普通可见字符）</td></tr>
          <tr><td><mark class="jesce">\\uXXXX</mark> 蓝底</td>
              <td><b>JSON 转义序列</b>（<code>\\uXXXX</code>、<code>\\n</code>…）—— 始终高亮</td></tr>
          <tr><td><span class="tkey k-reason">reasoning_content</span> / <span class="tkey k-content">content</span></td>
              <td>键名着色：思考 / 正文（显示顺序为“思考优先”）</td></tr>
          <tr><td class="bad">token 网格里的红格子</td>
              <td><b>按 token id 判定的特殊 token</b> —— 与开关无关（在 index.html 的 token 标签页里）</td></tr>
        </table>
        <div class="jt-lgsample">
          实时示例（跟着左侧开关变）：<code>${renderString(SAMPLE, vis())}</code>
          <span class="k">← 同一段文本 "a\u0007b\u200bc\ufeffd"</span>
        </div>`;
    }
    function vis() { return VIS; }
    function refreshLegend() { legendBox.innerHTML = legendHTML(); }
    legendBtn.onclick = () => {
      const show = legendBox.style.display === 'none';
      legendBox.style.display = show ? 'block' : 'none';
      if (show) refreshLegend();
    };
    visBox.onchange = () => { VIS = visBox.checked; rebuild(); if (legendBox.style.display !== 'none') refreshLegend(); };

    function rebuild() {
      tree.innerHTML = '';
      tree.append(node('$', root, '', 1));
      (opts.focus || []).forEach(key => {
        tree.querySelectorAll('.tnode').forEach(n => {
          if (n.dataset.key !== key || !n.classList.contains('composite')) return;
          setOpen(n, true);
          let p = n.parentElement;
          while (p && p !== tree) { if (p.classList && p.classList.contains('composite')) setOpen(p, true); p = p.parentElement; }
        });
      });
      updateStats();
    }

    /* --- 叶子 --- */
    function leafNode(key, v, path, depth) {
      const box = document.createElement('div'); box.className = 'tnode leaf';
      box.dataset.depth = depth; box.dataset.key = key;
      const head = document.createElement('div'); head.className = 'thead';
      const tgl = document.createElement('span'); tgl.className = 'tgl leafspace'; tgl.textContent = ' ';
      const k = document.createElement('span'); k.className = 'tkey ' + (KEYCOLOR[key] || ''); k.textContent = key;
      k.title = '点击复制路径：' + path; k.onclick = () => copyPath(path, k);
      head.append(tgl, k, document.createTextNode(': '));
      const t = typeOf(v);

      if (t !== 'string') {
        const s = document.createElement('span'); s.className = 'tscalar t-' + t;
        s.textContent = String(v); head.append(s); box.append(head); return box;
      }
      const text = v;
      const uc = countUncommon(text);
      if (uc.n) {                                        // 该字符串里有非常见字符 → 挂计数徽标
        const b = document.createElement('span'); b.className = 'ubadge';
        b.textContent = `非常见字符 ${uc.n}`;
        b.title = Object.entries(uc.kinds).map(([k, n]) => `${k} × ${n}`).join('\n');
        head.append(b);
      }
      const nBytes = new TextEncoder().encode(text).length;
      const byteBtn = document.createElement('button'); byteBtn.className = 'jt-more';
      byteBtn.textContent = `字节(${nBytes})`;
      const dump = document.createElement('pre'); dump.className = 'jt-hex'; dump.style.display = 'none';
      byteBtn.onclick = () => {
        if (dump.textContent === '') dump.textContent = hexdump(text, 4096);
        const show = dump.style.display === 'none';
        dump.style.display = show ? 'block' : 'none';
        byteBtn.textContent = show ? '收起字节' : `字节(${nBytes})`;
      };

      const span = document.createElement('span'); span.className = 'tscalar t-string';
      if (text.length > PREVIEW) {                       // 超长：预览 + 展开
        const pv = document.createElement('span');
        pv.innerHTML = '<span class="tq">"</span>' + renderString(text.slice(0, PREVIEW), VIS) + '<span class="tq">…"</span>';
        const more = document.createElement('button'); more.className = 'jt-more';
        more.textContent = `展开全文（${text.length} 字）`;
        more.onclick = () => {
          const full = document.createElement('span'); full.className = 'tscalar t-string';
          full.innerHTML = '<span class="tq">"</span>' + renderString(text, VIS) + '<span class="tq">"</span>';
          const less = document.createElement('button'); less.className = 'jt-more'; less.textContent = '收起';
          less.onclick = () => { full.replaceWith(pv); less.replaceWith(more); more.textContent = `展开全文（${text.length} 字）`; };
          pv.replaceWith(full); more.replaceWith(less);
        };
        span.append(pv, more);
      } else {
        span.innerHTML = '<span class="tq">"</span>' + renderString(text, VIS) + '<span class="tq">"</span>';
      }
      head.append(span, byteBtn);
      box.append(head, dump);
      return box;
    }

    /* --- 复合 --- */
    function node(key, v, path, depth) {
      const t = typeOf(v);
      if (t !== 'object' && t !== 'array') return leafNode(key, v, path, depth);
      const box = document.createElement('div'); box.className = 'tnode composite';
      box.dataset.depth = depth; box.dataset.key = key;
      const head = document.createElement('div'); head.className = 'thead';
      const tgl = document.createElement('span'); tgl.className = 'tgl'; tgl.textContent = '▸';
      const k = document.createElement('span'); k.className = 'tkey ' + (KEYCOLOR[key] || ''); k.textContent = key;
      k.title = '点击复制路径：' + path; k.onclick = () => copyPath(path, k);
      const n = Array.isArray(v) ? v.length : Object.keys(v).length;
      const meta = document.createElement('span'); meta.className = 'tmeta';
      meta.textContent = Array.isArray(v) ? `[ ${n} ]` : `{ ${n} }`;
      head.append(tgl, k, document.createTextNode(': '), meta);
      const kids = document.createElement('div'); kids.className = 'tkids';
      if (Array.isArray(v)) v.forEach((x, i) => kids.append(node('[' + i + ']', x, path + '[' + i + ']', depth + 1)));
      else {
        const { entries, changed } = orderedEntries(v, PRIORITY, PREFER);
        if (changed) {
          const b = document.createElement('span'); b.className = 'tbadge';
          b.textContent = '思考优先（显示顺序）';
          b.title = '原始 JSON 键顺序：' + Object.keys(v).join(', ');
          head.append(b);
        }
        entries.forEach(([kk, x]) => kids.append(node(kk, x, path ? path + '.' + kk : kk, depth + 1)));
      }
      const toggle = () => { const open = box.classList.toggle('open'); tgl.textContent = open ? '▾' : '▸'; };
      tgl.onclick = toggle; meta.onclick = toggle; meta.style.cursor = 'pointer';
      if (depth <= OPEN_DEPTH) { box.classList.add('open'); tgl.textContent = '▾'; }
      box.append(head, kids);
      return box;
    }

    /* --- 统计（含非常见字符汇总）--- */
    function updateStats() {
      let nodes = 0, chars = 0, ucTotal = 0; const kinds = {};
      (function walk(v) {
        nodes++; const t = typeOf(v);
        if (t === 'string') { chars += v.length; const u = countUncommon(v); ucTotal += u.n; for (const [k, n] of Object.entries(u.kinds)) kinds[k] = (kinds[k] || 0) + n; }
        else if (t === 'array') v.forEach(walk);
        else if (t === 'object') Object.values(v).forEach(walk);
      })(root);
      visCount.textContent = ucTotal ? `（本页 ${ucTotal} 个）` : '（本页 0 个 · 切换不会有变化）';
      visCount.className = 'jt-viscount' + (ucTotal ? ' has' : '');
      stats.innerHTML = `${nodes} 个节点 · 字符串 ${chars.toLocaleString()} 字`
        + (ucTotal ? ` · <b class="uc-hit">非常见字符 ${ucTotal}</b>` : ' · 非常见字符 0')
        + (ucTotal ? `<span class="uc-detail">（${Object.entries(kinds).map(([k, n]) => `${escDefault(k)}×${n}`).join('，')}）</span>` : '')
        + ` · 点键名可复制路径`;
    }

    bar.append(
      mk('展开全部', () => setOpenAll(() => true)),
      mk('折叠全部', () => setOpenAll(() => false)),
      mk(`展开 ${OPEN_DEPTH} 层`, () => setOpenAll(d => d <= OPEN_DEPTH)),
      visChk, legendBtn, filter, stats);

    filter.addEventListener('input', () => {
      const q = filter.value.trim().toLowerCase();
      wrap.querySelectorAll('.tnode').forEach(n => n.classList.remove('hit'));
      if (!q) return;
      wrap.querySelectorAll('.tnode').forEach(n => {
        if (!(n.dataset.key || '').toLowerCase().includes(q)) return;
        n.classList.add('hit');
        let p = n.parentElement;
        while (p && p !== tree) { if (p.classList && p.classList.contains('composite')) setOpen(p, true); p = p.parentElement; }
      });
    });

    wrap.append(bar, legendBox, tree);
    rebuild();
    return wrap;
  }

  /* --- UTF-8 字节 dump --- */
  function hexdump(text, limit = 4096) {
    const bytes = new TextEncoder().encode(text);
    const shown = bytes.slice(0, limit);
    const lines = [];
    for (let i = 0; i < shown.length; i += 16) {
      const row = shown.slice(i, i + 16);
      const h = [...row].map(b => b.toString(16).padStart(2, '0')).join(' ');
      const a = [...row].map(b => (b >= 32 && b < 127) ? String.fromCharCode(b) : '.').join('');
      lines.push(i.toString(16).padStart(6, '0') + '  ' + h.padEnd(47) + '  |' + a + '|');
    }
    if (bytes.length > limit) lines.push(`…（共 ${bytes.length} 字节，仅显示前 ${limit}）`);
    return lines.join('\n');
  }

  /* ================= 面板 ================= */
  function jsonPane(blob, { title, extra, hl, openDepth = 2, focus = [], vis = true } = {}) {
    let preferReasoning = true;
    const treeVisState = { on: vis };
    const treeVis = () => treeVisState.on;
    const card = document.createElement('div'); card.className = 'card';
    const hd = document.createElement('div'); hd.className = 'hd';
    const h2 = document.createElement('h2'); h2.textContent = title;
    const k = document.createElement('span'); k.className = 'k';
    k.innerHTML = `<b>${blob.bytes}</b> 字节 · sha256 <b>${blob.sha256.slice(0, 16)}…</b> · ${blob.name}`;
    hd.append(h2, k);

    const holder = document.createElement('pre');
    holder.className = 'raw-holder';
    holder.textContent = blob.text; holder.style.display = 'none';
    holder.dataset.expectBytes = blob.bytes; holder.dataset.expectSha = blob.sha256; holder.dataset.name = blob.name;

    const bar = document.createElement('div'); bar.className = 'bar';
    const pre = document.createElement('pre');
    let parsed = null, parseErr = null;
    try { parsed = JSON.parse(blob.text); } catch (e) { parseErr = e.message; }
    const treeWrap = document.createElement('div'); treeWrap.style.display = 'none';
    let treeBuilt = false;

    const bRaw = document.createElement('button'); bRaw.textContent = '单行/原文';
    const bFmt = document.createElement('button'); bFmt.textContent = '缩进';
    const bTree = document.createElement('button'); bTree.textContent = '树视图（可折叠）';
    const bDl = document.createElement('button'); bDl.textContent = '下载原文';
    const bChk = document.createElement('button'); bChk.textContent = '自检';
    const chk = document.createElement('span'); chk.className = 'hint';
    if (!parsed) { bFmt.disabled = bTree.disabled = true; bFmt.title = bTree.title = '不是合法 JSON：' + parseErr; }

    let mode = parsed ? 'tree' : 'raw';
    function apply() {
      bRaw.classList.toggle('on', mode === 'raw');
      bFmt.classList.toggle('on', mode === 'fmt');
      bTree.classList.toggle('on', mode === 'tree');
      pre.style.display = (mode === 'tree') ? 'none' : 'block';
      treeWrap.style.display = (mode === 'tree') ? 'block' : 'none';
      if (mode === 'raw') pre.innerHTML = renderJsonText(blob.text, treeVis());
      if (mode === 'fmt') pre.innerHTML = renderJsonText(parsed ? JSON.stringify(parsed, null, 2) : blob.text, treeVis());
      if (mode === 'tree' && !treeBuilt && parsed) {
        const t = jsonTree(parsed, { openDepth, focus, preferReasoning, vis: treeVisState.on });
        const box = t.querySelector('.jt-chk input');             // 树内开关同步给文本视图
        if (box) box.addEventListener('change', () => { treeVisState.on = box.checked; apply(); });
        treeWrap.append(t);
        treeBuilt = true;
      }
    }
    bRaw.onclick = () => { mode = 'raw'; apply(); };
    bFmt.onclick = () => { mode = 'fmt'; apply(); };
    bTree.onclick = () => { mode = 'tree'; apply(); };
    bDl.onclick = () => {
      const b = new Blob([holder.textContent], { type: 'application/octet-stream' });
      const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = blob.name; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    bChk.onclick = () => {
      const txt = holder.textContent;
      const bytes = new TextEncoder().encode(txt);
      const sha = global.__sha256bytes(bytes);
      const okB = bytes.length === Number(blob.bytes), okS = sha === blob.sha256;
      chk.innerHTML = `<span class="${(okB && okS) ? 'ok' : 'bad'}">${(okB && okS) ? '一致 ✅' : '差异 ❌'}</span> `
        + `${bytes.length} 字节 / sha256 ${sha.slice(0, 16)}…`;
    };
    const lab = document.createElement('label'); lab.className = 'jt-chk';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true;
    lab.append(cb, document.createTextNode(' reasoning_content 排在 content 前（只改显示顺序）'));
    cb.onchange = () => {
      preferReasoning = cb.checked;
      if (treeBuilt) { treeWrap.innerHTML = ''; treeBuilt = false; }
      if (mode === 'tree') apply();
    };
    bar.append(bRaw, bFmt, bTree, lab, bDl, bChk, chk);
    card.append(hd, bar, holder, pre, treeWrap);
    if (extra) { const e = document.createElement('div'); e.className = 'hint'; e.innerHTML = extra; card.append(e); }
    apply();
    return card;
  }

  global.jsonTree = jsonTree;
  global.jsonPane = jsonPane;
  global.renderJsonText = renderJsonText;
  global.classifyChar = classify;
  global.renderString = renderString;
})(window);
