/* Sertifikat / ruxsatnoma dizayneri (Canva'ga o'xshash).
 *
 * Kanvas — SVG (viewBox = chiqish o'lchami pikselda). Matnni joylashtirish (o'lchash, qatorlarga
 * bo'lish, sig'dirish) serverdagi app/services/design.py dagi algoritm bilan AYNAN bir xil va
 * serverdan kelgan harf kengliklari bilan bajariladi — shuning uchun PDF ekrandagidek chiqadi.
 */
(function () {
  'use strict';

  var DATA = JSON.parse(document.getElementById('dz-data').textContent);
  var TID = DATA.id, CSRF = DATA.csrf, KIND = DATA.kind;
  var design = DATA.design;
  var NS = 'http://www.w3.org/2000/svg';
  var FALLBACK = 'dejavu-sans';
  var REPL = {'ʻ': "‘'", 'ʼ': "’'", '‘': "'", '’': "'", '`': "'", '“': '"', '”': '"', '«': '"', '»': '"',
    '—': '-', '–': '-', '№': 'N', '…': '.', ' ': ' '};
  var PAGE_MM = {A4L: [297, 210], A4P: [210, 297], A5L: [210, 148], A5P: [148, 210]};

  var FONTS = null, CHARIDX = new Map();
  var W = 1754, H = 1240;
  var sel = null;               // tanlangan element id
  var zoom = 1, fitZoom = 1;
  var undoStack = [], redoStack = [], lastState = JSON.stringify(design), histTimer = null;
  var saveTimer = null, saving = false, dirty = false, saveAgain = false;
  var showSample = true, sampleName = '';
  var SAMPLE = {};
  DATA.placeholders.forEach(function (p) { SAMPLE[p.key] = p.sample; });
  var ASSET_SIZE = {};          // rasm nomi → [w, h]
  var clipboard = null;
  var drag = null;              // joriy sichqoncha amali
  var guides = [];
  var rafPending = false;

  var stage = document.getElementById('dz-stage');
  var wrap = document.getElementById('dz-wrap');
  var props = document.getElementById('dz-props');
  var layers = document.getElementById('dz-layers');
  var statusEl = document.getElementById('dz-status');

  // ------------------------------------------------------------------ yordamchilar
  function $(q, root) { return (root || document).querySelector(q); }
  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function r2(v) { return Math.round(v * 100) / 100; }
  function byId(id) { for (var i = 0; i < design.els.length; i++) if (design.els[i].id === id) return design.els[i]; return null; }
  function idx(id) { for (var i = 0; i < design.els.length; i++) if (design.els[i].id === id) return i; return -1; }
  function selected() { return sel ? byId(sel) : null; }
  function newId() { var i = design.els.length + 1; while (byId('e' + i)) i++; return 'e' + i; }
  function assetUrl(name) { return '/admin/certificates/asset/' + name; }
  function hex6(c) {
    c = (c || '#000000').replace('#', '');
    if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
    return '#' + c.slice(0, 6);
  }
  function alphaOf(c) { c = (c || '').replace('#', ''); return c.length === 8 ? parseInt(c.slice(6, 8), 16) / 255 : 1; }
  function withAlpha(c6, a) { var v = Math.round(clamp(a, 0, 1) * 255).toString(16); return hex6(c6) + (v.length < 2 ? '0' + v : v); }

  // ------------------------------------------------------------------ sahifa o'lchami (serverdagi page_size bilan bir xil)
  function pageSize(page) {
    var dpi = page.dpi || 150;
    if (PAGE_MM[page.preset]) return [Math.round(PAGE_MM[page.preset][0] / 25.4 * dpi), Math.round(PAGE_MM[page.preset][1] / 25.4 * dpi)];
    if (page.preset === 'custom') {
      var w = Math.round(clamp(page.w_mm || 297, 50, 1000) / 25.4 * dpi), h = Math.round(clamp(page.h_mm || 210, 50, 1000) / 25.4 * dpi);
      var k = Math.min(1, 3508 / Math.max(w, h));
      return [Math.max(100, Math.round(w * k)), Math.max(100, Math.round(h * k))];
    }
    return [page.w || 1754, page.h || 1240];
  }
  function pageMM() { var dpi = design.page.dpi || 150; return [W / dpi * 25.4, H / dpi * 25.4]; }
  // Birliklar: foiz ↔ mm ↔ pt (foydalanuvchiga tushunarli ko'rinish)
  function pctToMM(p, axis) { return p * pageMM()[axis] / 100; }
  function mmToPct(mm, axis) { return mm / pageMM()[axis] * 100; }
  function sizeToPt(s) { return s * pageMM()[0] / 100 / 25.4 * 72; }
  function ptToSize(pt) { return pt * 25.4 / 72 / pageMM()[0] * 100; }

  // ------------------------------------------------------------------ shriftlar va matn o'lchami
  function fontEntry(fam) { return FONTS.fonts[fam] || FONTS.fonts[FALLBACK]; }
  function famKey(fam) { return FONTS.fonts[fam] ? fam : FALLBACK; }
  function fileOf(fam, bold) {
    var f = fontEntry(fam), v = bold && f.files.b ? 'b' : 'r';
    return {m: f.files[v], css: 'F_' + famKey(fam).replace(/[^a-z0-9]/gi, '_') + '_' + v, fam: famKey(fam), v: v};
  }
  var loadedFaces = {};
  function ensureFont(fam, bold) {
    if (!FONTS) return Promise.resolve();
    var f = fileOf(fam, bold);
    if (loadedFaces[f.css]) return loadedFaces[f.css];
    var face = new FontFace(f.css, 'url(' + f.m.url + ')');
    document.fonts.add(face);
    loadedFaces[f.css] = face.load().then(function () { schedule(); }, function () {});
    return loadedFaces[f.css];
  }
  function adv(m, ch) { var i = CHARIDX.get(ch.codePointAt(0)); return i === undefined ? -1 : m.adv[i]; }
  function has(m, ch) { return adv(m, ch) >= 0; }

  function prepare(text, fam, bold) {
    var f = fileOf(fam, bold), m = f.m, out = [], missing = false;
    for (var ch of text) {
      if (ch === '\n' || has(m, ch)) { out.push(ch); continue; }
      var rep = null, cands = REPL[ch] || '';
      for (var r of cands) { if (has(m, r)) { rep = r; break; } }
      if (rep !== null) out.push(rep); else { out.push(ch); missing = true; }
    }
    var t = out.join('');
    if (missing && f.fam !== FALLBACK) return prepare(t, FALLBACK, bold);
    if (missing) t = Array.from(t).filter(function (c) { return c === '\n' || has(m, c); }).join('');
    return {text: t, f: f};
  }
  function textWidth(s, m, size, ls) {
    var a = 0, n = 0;
    for (var ch of s) { var v = adv(m, ch); a += v < 0 ? 0 : v; n++; }
    return a * size + ls * size * n;
  }
  function wrapLines(text, m, size, ls, maxW, wrapOn) {
    var lines = [];
    text.split('\n').forEach(function (para) {
      if (!wrapOn) { lines.push(para); return; }
      var words = para.split(' '), line = words[0];
      for (var i = 1; i < words.length; i++) {
        var cand = line + ' ' + words[i];
        if (textWidth(cand, m, size, ls) <= maxW) line = cand;
        else { lines.push(line); line = words[i]; }
      }
      lines.push(line);
    });
    return lines;
  }
  function fillText(text, ctx) {
    return text.replace(/\{([a-z_]+)\}/g, function (all, k) { return Object.prototype.hasOwnProperty.call(ctx, k) ? String(ctx[k]) : all; });
  }
  function context() {
    if (!showSample) return {};
    var c = Object.assign({}, SAMPLE);
    if (sampleName) {
      c.full_name = sampleName;
      var parts = sampleName.split(' ');
      c.last_name = parts[0]; c.first_name = parts.slice(1).join(' ') || parts[0];
    }
    return c;
  }
  // design.py → layout_text bilan bir xil
  function layoutText(el, ctx) {
    var raw = fillText(el.text || '', ctx);
    if (el.upper) raw = raw.toUpperCase();
    var p = prepare(raw, el.font, el.bold), m = p.f.m;
    var bw = el.w * W / 100, bh = el.h * H / 100;
    var size = el.size * W / 100;
    var minSize = Math.max(4.0, size * 0.2);
    var ls = el.ls || 0, lh = el.lh || 1.2, wrapOn = el.wrap !== false, fit = el.fit !== false, lines, i;
    while (true) {
      lines = wrapLines(p.text, m, size, ls, bw, wrapOn);
      var widest = 0;
      for (i = 0; i < lines.length; i++) widest = Math.max(widest, textWidth(lines[i], m, size, ls));
      var blockH = lines.length * lh * size;
      if (!fit || size <= minSize) break;
      if (widest <= bw + 0.5 && (!wrapOn || blockH <= bh + 0.5)) break;
      size = Math.max(minSize, size * 0.95);
    }
    var L = lh * size, total = lines.length * L;
    var top = el.valign === 'top' ? 0.0 : (el.valign === 'bottom' ? bh - total : (bh - total) / 2);
    var xs = [], bases = [];
    for (i = 0; i < lines.length; i++) {
      var w = textWidth(lines[i], m, size, ls);
      xs.push(el.align === 'left' ? 0.0 : (el.align === 'right' ? bw - w : (bw - w) / 2));
      bases.push(top + i * L + (L - (m.asc + m.desc) * size) / 2 + m.asc * size);
    }
    return {lines: lines, size: size, f: p.f, m: m, xs: xs, bases: bases, ls: ls};
  }

  // ------------------------------------------------------------------ chizish (SVG)
  var root, defs, layerEls, layerOver;
  function buildStage() {
    stage.innerHTML = '';
    root = svg('svg', {viewBox: '0 0 ' + W + ' ' + H, class: 'dz-svg'}, stage);
    defs = svg('defs', {}, root);
    layerEls = svg('g', {}, root);
    layerOver = svg('g', {class: 'dz-over'}, root);
    root.addEventListener('pointerdown', onDown);
    root.addEventListener('dblclick', onDbl);
  }
  function applyZoom() {
    root.setAttribute('width', W * zoom);
    root.setAttribute('height', H * zoom);
    $('#dz-zoom').textContent = Math.round(zoom / fitZoom * 100) + '%';
  }
  function schedule() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () { rafPending = false; draw(); });
  }
  function draw() {
    if (!FONTS || !root) return;
    defs.innerHTML = ''; layerEls.innerHTML = '';
    drawBackground();
    var ctx = context();
    design.els.forEach(function (el) { if (!el.hidden) drawEl(el, ctx); });
    drawOverlay();
  }
  function drawBackground() {
    var bg = design.bg;
    svg('rect', {x: 0, y: 0, width: W, height: H, fill: hex6(bg.color || '#ffffff')}, layerEls);
    if (bg.type === 'gradient') {
      var a = (bg.angle || 0) * Math.PI / 180, dx = Math.sin(a), dy = -Math.cos(a);
      var L = Math.abs(W * dx) + Math.abs(H * dy) || 1, cx = W / 2, cy = H / 2;
      var g = svg('linearGradient', {id: 'bgG', gradientUnits: 'userSpaceOnUse',
        x1: cx - dx * L / 2, y1: cy - dy * L / 2, x2: cx + dx * L / 2, y2: cy + dy * L / 2}, defs);
      svg('stop', {offset: 0, 'stop-color': hex6(bg.color)}, g);
      svg('stop', {offset: 1, 'stop-color': hex6(bg.color2)}, g);
      svg('rect', {x: 0, y: 0, width: W, height: H, fill: 'url(#bgG)'}, layerEls);
    } else if (bg.type === 'image' && bg.image) {
      svg('image', {href: assetUrl(bg.image), x: 0, y: 0, width: W, height: H,
        preserveAspectRatio: {cover: 'xMidYMid slice', contain: 'xMidYMid meet', stretch: 'none'}[bg.fit || 'cover']}, layerEls);
    }
  }
  function box(el) {
    var x = el.x * W / 100, y = el.y * H / 100, w = el.w * W / 100, h = el.h * H / 100;
    return {x: x, y: y, w: w, h: h, cx: x + w / 2, cy: y + h / 2};
  }
  function drawEl(el, ctx) {
    var b = box(el);
    var g = svg('g', {'data-id': el.id, opacity: el.opacity < 1 ? el.opacity : null,
      transform: el.rot ? 'rotate(' + el.rot + ' ' + b.cx + ' ' + b.cy + ')' : null}, layerEls);
    if (el.type === 'text') drawText(el, g, b, ctx);
    else if (el.type === 'image') drawImage(el, g, b);
    else if (el.type === 'rect') {
      var sw = (el.sw || 0) * W / 100, st = el.stroke && sw > 0;
      var r = Math.max(0, (el.radius || 0) / 100 * Math.min(b.w, b.h) / 2 - (st ? sw / 2 : 0));
      svg('rect', {x: b.x + (st ? sw / 2 : 0), y: b.y + (st ? sw / 2 : 0), width: Math.max(0, b.w - (st ? sw : 0)),
        height: Math.max(0, b.h - (st ? sw : 0)), rx: r, ry: r, fill: el.fill ? hex6(el.fill) : 'none',
        'fill-opacity': el.fill ? alphaOf(el.fill) : null,
        stroke: st ? hex6(el.stroke) : null, 'stroke-width': st ? sw : null}, g);
    } else if (el.type === 'ellipse') {
      var sw2 = (el.sw || 0) * W / 100, st2 = el.stroke && sw2 > 0;
      svg('ellipse', {cx: b.cx, cy: b.cy, rx: Math.max(0, b.w / 2 - (st2 ? sw2 / 2 : 0)), ry: Math.max(0, b.h / 2 - (st2 ? sw2 / 2 : 0)),
        fill: el.fill ? hex6(el.fill) : 'none', 'fill-opacity': el.fill ? alphaOf(el.fill) : null,
        stroke: st2 ? hex6(el.stroke) : null, 'stroke-width': st2 ? sw2 : null}, g);
    } else if (el.type === 'line') {
      var lw = Math.max(1, (el.sw || 0.1) * W / 100);
      svg('rect', {x: b.x, y: b.cy - lw / 2, width: b.w, height: lw, fill: hex6(el.stroke || '#111827')}, g);
    }
    // Bosish uchun ko'rinmas maydon (butun quti)
    var minH = el.type === 'line' ? Math.max(b.h, 14 / zoom) : b.h;
    svg('rect', {x: b.x, y: b.cy - minH / 2, width: b.w, height: minH, fill: 'transparent', class: 'dz-hit'}, g);
  }
  function drawText(el, g, b, ctx) {
    var lay = layoutText(el, ctx);
    ensureFont(lay.f.fam, lay.f.v === 'b');
    var host = g, sw = el.stroke ? el.stroke.w * W / 100 : 0;
    if (el.shadow) {
      var fid = 'sh_' + el.id;
      var flt = svg('filter', {id: fid, x: '-50%', y: '-50%', width: '200%', height: '200%', 'color-interpolation-filters': 'sRGB'}, defs);
      svg('feDropShadow', {dx: el.shadow.dx * W / 100, dy: el.shadow.dy * W / 100, stdDeviation: el.shadow.blur * W / 100 / 2,
        'flood-color': hex6(el.shadow.color), 'flood-opacity': alphaOf(el.shadow.color)}, flt);
      host = svg('g', {filter: 'url(#' + fid + ')'}, g);
    }
    function lines(fill, stroke) {
      lay.lines.forEach(function (s, i) {
        var x = b.x + lay.xs[i], xs = [], chars = '';
        for (var ch of s) {
          if (!/\s/.test(ch)) { xs.push(r2(x)); chars += ch; }
          var a = adv(lay.m, ch);
          x += (a < 0 ? 0 : a) * lay.size + lay.ls * lay.size;
        }
        if (!chars) return;
        var t = svg('text', {x: xs.join(' '), y: r2(b.y + lay.bases[i]), 'font-family': lay.f.css, 'font-size': lay.size,
          fill: fill, stroke: stroke ? fill : null, 'stroke-width': stroke ? 2 * sw : null, 'stroke-linejoin': stroke ? 'round' : null}, host);
        t.textContent = chars;
      });
    }
    if (el.stroke) lines(hex6(el.stroke.color), true);
    lines(hex6(el.color), false);
  }
  function drawImage(el, g, b) {
    var par = {contain: 'xMidYMid meet', cover: 'xMidYMid slice', stretch: 'none'}[el.fit || 'contain'];
    var img = {href: assetUrl(el.asset), x: b.x, y: b.y, width: b.w, height: b.h, preserveAspectRatio: par};
    if (el.radius > 0) {
      var sz = ASSET_SIZE[el.asset], fx = b.x, fy = b.y, fw = b.w, fh = b.h;
      if (sz && el.fit === 'contain') {
        var k = Math.min(b.w / sz[0], b.h / sz[1]); fw = sz[0] * k; fh = sz[1] * k; fx = b.x + (b.w - fw) / 2; fy = b.y + (b.h - fh) / 2;
      } else if (!sz) loadAssetSize(el.asset);
      var r = el.radius / 100 * Math.min(fw, fh) / 2, cid = 'cl_' + el.id;
      var cp = svg('clipPath', {id: cid}, defs);
      svg('rect', {x: fx, y: fy, width: fw, height: fh, rx: r, ry: r}, cp);
      img['clip-path'] = 'url(#' + cid + ')';
    }
    svg('image', img, g);
  }
  function loadAssetSize(name) {
    if (ASSET_SIZE[name] === 0) return;
    ASSET_SIZE[name] = 0;
    var im = new Image();
    im.onload = function () { ASSET_SIZE[name] = [im.naturalWidth, im.naturalHeight]; schedule(); };
    im.src = assetUrl(name);
  }

  // Tanlov ramkasi va tutqichlar
  var HANDLES = [['nw', -1, -1], ['n', 0, -1], ['ne', 1, -1], ['e', 1, 0], ['se', 1, 1], ['s', 0, 1], ['sw', -1, 1], ['w', -1, 0]];
  function drawOverlay() {
    layerOver.innerHTML = '';
    guides.forEach(function (gd) {
      if (gd.axis === 'x') svg('line', {x1: gd.v, y1: 0, x2: gd.v, y2: H, class: 'dz-guide', 'stroke-width': 1 / zoom}, layerOver);
      else svg('line', {x1: 0, y1: gd.v, x2: W, y2: gd.v, class: 'dz-guide', 'stroke-width': 1 / zoom}, layerOver);
    });
    var el = selected();
    if (!el || el.hidden) return;
    var b = box(el), hs = 9 / zoom, g = svg('g', {transform: el.rot ? 'rotate(' + el.rot + ' ' + b.cx + ' ' + b.cy + ')' : null}, layerOver);
    svg('rect', {x: b.x, y: b.y, width: b.w, height: b.h, class: 'dz-sel' + (el.locked ? ' locked' : ''), 'stroke-width': 1.5 / zoom}, g);
    if (el.locked) return;
    HANDLES.forEach(function (h) {
      if (el.type === 'line' && h[2] !== 0) return;
      svg('rect', {x: b.cx + h[1] * b.w / 2 - hs / 2, y: b.cy + h[2] * b.h / 2 - hs / 2, width: hs, height: hs,
        class: 'dz-handle', 'data-handle': h[0], 'stroke-width': 1.2 / zoom}, g);
    });
    var ry = b.y - 26 / zoom;
    svg('line', {x1: b.cx, y1: b.y, x2: b.cx, y2: ry, class: 'dz-sel', 'stroke-width': 1 / zoom}, g);
    svg('circle', {cx: b.cx, cy: ry, r: 6 / zoom, class: 'dz-handle rot', 'data-handle': 'rot', 'stroke-width': 1.2 / zoom}, g);
  }

  // ------------------------------------------------------------------ sichqoncha / sensor
  function toPage(e) {
    var pt = root.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    var p = pt.matrixTransform(root.getScreenCTM().inverse());
    return {x: p.x, y: p.y};
  }
  function onDown(e) {
    if (e.button !== 0) return;
    var p = toPage(e), handle = e.target.getAttribute('data-handle');
    var el = selected();
    if (handle && el) {
      e.preventDefault();
      drag = {mode: handle === 'rot' ? 'rot' : 'resize', h: handle, start: p, orig: JSON.parse(JSON.stringify(el))};
      root.setPointerCapture(e.pointerId);
      return;
    }
    var g = e.target.closest('g[data-id]');
    if (g) {
      e.preventDefault();
      var id = g.getAttribute('data-id');
      if (sel !== id) select(id);
      el = selected();
      if (el && !el.locked) {
        drag = {mode: 'move', start: p, orig: JSON.parse(JSON.stringify(el))};
        root.setPointerCapture(e.pointerId);
      }
      return;
    }
    select(null);
  }
  function onMove(e) {
    if (!drag) return;
    var el = selected();
    if (!el) { drag = null; return; }
    var p = toPage(e), o = drag.orig;
    if (drag.mode === 'move') {
      var nx = o.x + (p.x - drag.start.x) / W * 100, ny = o.y + (p.y - drag.start.y) / H * 100;
      var s = e.altKey ? {x: nx, y: ny, g: []} : snap(el, nx, ny);
      el.x = s.x; el.y = s.y; guides = s.g;
    } else if (drag.mode === 'rot') {
      var b = box(o), ang = Math.atan2(p.y - b.cy, p.x - b.cx) * 180 / Math.PI + 90;
      if (e.shiftKey) ang = Math.round(ang / 15) * 15;
      else { var near = Math.round(ang / 90) * 90; if (Math.abs(ang - near) < 3) ang = near; }
      ang = ((ang + 180) % 360 + 360) % 360 - 180;
      el.rot = Math.round(ang * 10) / 10;
    } else resize(el, o, drag.h, p, e.shiftKey);
    schedule(); syncGeometry(); touch();
  }
  function onUp() { if (drag) { drag = null; guides = []; schedule(); syncGeometry(); touch(); } }
  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);

  function snap(el, nx, ny) {
    var th = 6 / zoom, w = el.w * W / 100, h = el.h * H / 100, x = nx * W / 100, y = ny * H / 100, g = [];
    var tx = [0, W / 2, W], ty = [0, H / 2, H];
    design.els.forEach(function (o) {
      if (o.id === el.id || o.hidden) return;
      var b = box(o); tx.push(b.x, b.cx, b.x + b.w); ty.push(b.y, b.cy, b.y + b.h);
    });
    function best(pos, size, targets) {
      var cands = [pos, pos + size / 2, pos + size], res = null;
      cands.forEach(function (c, i) {
        targets.forEach(function (t) {
          var d = Math.abs(c - t);
          if (d <= th && (!res || d < res.d)) res = {d: d, shift: t - c, v: t};
        });
      });
      return res;
    }
    var bx = best(x, w, tx), by = best(y, h, ty);
    if (bx) { x += bx.shift; g.push({axis: 'x', v: bx.v}); }
    if (by) { y += by.shift; g.push({axis: 'y', v: by.v}); }
    return {x: x / W * 100, y: y / H * 100, g: g};
  }
  function resize(el, o, h, p, keep) {
    var b = box(o), a = (o.rot || 0) * Math.PI / 180, cos = Math.cos(a), sin = Math.sin(a);
    var dx = p.x - drag.start.x, dy = p.y - drag.start.y;
    var lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos;   // burilgan o'qlarda
    var hx = h.indexOf('e') >= 0 ? 1 : (h.indexOf('w') >= 0 ? -1 : 0);
    var hy = h.indexOf('s') >= 0 ? 1 : (h.indexOf('n') >= 0 ? -1 : 0);
    var corner = hx !== 0 && hy !== 0;
    var nw = Math.max(4, b.w + hx * lx), nh = Math.max(4, b.h + hy * ly);
    // Burchakdan: rasm va matn nisbati saqlanadi (Shift — erkin); shakl — Shift bilan saqlanadi
    var keepAspect = corner && ((el.type === 'image' || el.type === 'text') ? !keep : keep);
    if (keepAspect) {
      var k = Math.max(nw / b.w, nh / b.h);
      nw = b.w * k; nh = b.h * k;
    }
    if (hx === 0) nw = b.w;
    if (hy === 0) nh = b.h;
    var sx = hx * (nw - b.w) / 2, sy = hy * (nh - b.h) / 2;   // markaz siljishi (lokal)
    var cx = b.cx + sx * cos - sy * sin, cy = b.cy + sx * sin + sy * cos;
    el.w = nw / W * 100; el.h = nh / H * 100;
    el.x = (cx - nw / 2) / W * 100; el.y = (cy - nh / 2) / H * 100;
    if (el.type === 'text' && corner && keepAspect) el.size = Math.max(0.2, o.size * nw / b.w);
  }
  function onDbl(e) {
    var g = e.target.closest('g[data-id]');
    if (!g) return;
    var el = byId(g.getAttribute('data-id'));
    if (el && el.type === 'text') { var ta = $('#p-text'); if (ta) { ta.focus(); ta.select(); } }
  }

  // ------------------------------------------------------------------ tarix (orqaga/oldinga) va saqlash
  function touch() {
    clearTimeout(histTimer);
    histTimer = setTimeout(commitHistory, 350);
    markDirty();
  }
  function commitHistory() {
    histTimer = null;
    var now = JSON.stringify(design);
    if (now === lastState) return;
    undoStack.push(lastState);
    if (undoStack.length > 100) undoStack.shift();
    redoStack = [];
    lastState = now;
  }
  function restore(state) {
    design = JSON.parse(state);
    lastState = state;
    var ps = pageSize(design.page); W = ps[0]; H = ps[1];
    if (sel && !byId(sel)) sel = null;
    rebuild();
    markDirty();
  }
  function undo() { if (histTimer) { clearTimeout(histTimer); commitHistory(); } if (undoStack.length) { redoStack.push(lastState); restore(undoStack.pop()); } }
  function redo() { if (redoStack.length) { undoStack.push(lastState); restore(redoStack.pop()); } }

  function setStatus(t, cls) { statusEl.textContent = t; statusEl.className = 'small ' + (cls || 'muted'); }
  function markDirty() {
    dirty = true;
    setStatus('● saqlanmagan o\'zgarishlar');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1500);
  }
  function save() {
    clearTimeout(saveTimer);
    if (saving) { saveAgain = true; return Promise.resolve(); }
    if (!dirty) return Promise.resolve();
    saving = true; dirty = false;
    setStatus('Saqlanmoqda…');
    return fetch('/admin/certificates/' + TID + '/design', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF}, body: JSON.stringify(design)})
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.status); });
        return r.json();
      })
      .then(function (j) {
        if (j.w && (j.w !== W || j.h !== H)) { W = j.w; H = j.h; design.page.w = W; design.page.h = H; rebuild(); }
        setStatus(dirty ? '● saqlanmagan o\'zgarishlar' : '✓ Saqlandi', dirty ? 'muted' : 'ok-text');
      })
      .catch(function (e) { dirty = true; setStatus('⚠️ Saqlab bo\'lmadi: ' + String(e.message).slice(0, 120), 'err-text'); })
      .finally(function () { saving = false; if (saveAgain || dirty) { saveAgain = false; if (dirty) saveTimer = setTimeout(save, 1500); } });
  }
  window.addEventListener('beforeunload', function (e) { if (dirty || saving) { e.preventDefault(); e.returnValue = ''; } });

  // ------------------------------------------------------------------ elementlar
  function defaults(type) {
    var base = {id: newId(), type: type, name: '', x: 35, y: 40, w: 30, h: 12, rot: 0, opacity: 1, locked: false, hidden: false};
    if (type === 'text') return Object.assign(base, {text: 'Yangi matn', font: 'montserrat', bold: false, italic: false,
      size: 2.6, color: '#111827', align: 'center', valign: 'middle', lh: 1.2, ls: 0, upper: false, wrap: true, fit: true,
      shadow: null, stroke: null, x: 25, w: 50, h: 8});
    if (type === 'rect') return Object.assign(base, {fill: '#dbeafe', stroke: '', sw: 0.2, radius: 0, h: 30 * W / H * 0.6});
    if (type === 'ellipse') { var s = 14; return Object.assign(base, {fill: '#fde68a', stroke: '', sw: 0.2, x: 43, y: 40, w: s, h: s * W / H}); }
    if (type === 'line') return Object.assign(base, {fill: '', stroke: '#111827', sw: 0.15, x: 30, y: 50, w: 40, h: 2});
    return base;
  }
  function add(el) {
    design.els.push(el);
    select(el.id);
    schedule(); touch();
  }
  function addVar(key) {
    var el = selected(), ta = $('#p-text');
    if (el && el.type === 'text' && ta && document.activeElement === ta) { insertAtCursor(ta, '{' + key + '}'); return; }
    var t = defaults('text');
    t.text = '{' + key + '}';
    var ph = DATA.placeholders.filter(function (p) { return p.key === key; })[0];
    t.name = ph ? ph.label : key;
    if (key === 'full_name') { t.size = 4.6; t.font = 'cormorant'; t.bold = true; t.wrap = false; t.h = 12; t.x = 10; t.w = 80; t.y = 44; }
    add(t);
  }
  function insertAtCursor(ta, s) {
    var a = ta.selectionStart || 0, b = ta.selectionEnd || a;
    ta.value = ta.value.slice(0, a) + s + ta.value.slice(b);
    ta.selectionStart = ta.selectionEnd = a + s.length;
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    ta.focus();
  }
  function remove() {
    var i = idx(sel); if (i < 0) return;
    design.els.splice(i, 1); select(null); schedule(); touch();
  }
  function duplicate(src) {
    src = src || selected(); if (!src) return;
    var c = JSON.parse(JSON.stringify(src));
    c.id = newId(); c.x += 2; c.y += 2; c.locked = false;
    add(c);
  }
  function reorder(dir) {
    var i = idx(sel); if (i < 0) return;
    var el = design.els.splice(i, 1)[0], j;
    if (dir === 'top') j = design.els.length; else if (dir === 'bottom') j = 0;
    else j = clamp(i + dir, 0, design.els.length);
    design.els.splice(j, 0, el);
    schedule(); renderLayers(); touch();
  }
  function align(how) {
    var el = selected(); if (!el) return;
    if (how === 'h') el.x = (100 - el.w) / 2; else el.y = (100 - el.h) / 2;
    schedule(); syncGeometry(); touch();
  }

  function uploadAsset(file) {
    var fd = new FormData(); fd.append('file', file); fd.append('csrf', CSRF);
    setStatus('Rasm yuklanmoqda…');
    return fetch('/admin/certificates/' + TID + '/asset', {method: 'POST', body: fd, credentials: 'same-origin', headers: {'X-CSRF-Token': CSRF}})
      .then(function (r) { if (!r.ok) return r.text().then(function (t) { throw new Error(t); }); return r.json(); })
      .then(function (j) { ASSET_SIZE[j.name] = [j.w, j.h]; setStatus(''); return j; })
      .catch(function (e) { setStatus('⚠️ ' + e.message, 'err-text'); throw e; });
  }
  function addImage(file) {
    uploadAsset(file).then(function (j) {
      var t = defaults('image'), wpx = W * 0.25, hpx = wpx * j.h / Math.max(1, j.w);
      if (hpx > H * 0.5) { hpx = H * 0.5; wpx = hpx * j.w / Math.max(1, j.h); }
      Object.assign(t, {asset: j.name, fit: 'contain', radius: 0, w: wpx / W * 100, h: hpx / H * 100});
      t.x = (100 - t.w) / 2; t.y = (100 - t.h) / 2;
      add(t);
    });
  }

  // ------------------------------------------------------------------ tanlash va panellar
  function select(id) {
    sel = id;
    schedule();
    renderProps();
    renderLayers();
  }
  function rebuild() {
    var rel = fitZoom ? zoom / fitZoom : 1;
    buildStage(); fitView(false); zoom = fitZoom * rel; applyZoom();
    schedule(); renderProps(); renderLayers();
  }

  function num(label, key, val, step, min, max, unit) {
    return '<div class="field"><label>' + label + (unit ? ' <span class="muted">(' + unit + ')</span>' : '') + '</label>' +
      '<input type="number" data-k="' + key + '" value="' + r2(val) + '" step="' + step + '"' +
      (min !== undefined ? ' min="' + min + '"' : '') + (max !== undefined ? ' max="' + max + '"' : '') + '></div>';
  }
  function color(label, key, val) {
    return '<div class="field"><label>' + label + '</label><input type="color" data-k="' + key + '" value="' + hex6(val) + '"></div>';
  }
  function check(label, key, on, disabled) {
    return '<label class="check"><input type="checkbox" data-k="' + key + '"' + (on ? ' checked' : '') + (disabled ? ' disabled' : '') + '> ' + label + '</label>';
  }
  function seg(key, cur, opts) {
    return '<div class="dz-seg">' + opts.map(function (o) {
      return '<button type="button" data-seg="' + key + '" data-v="' + o[0] + '" class="' + (cur === o[0] ? 'on' : '') + '" title="' + o[2] + '">' + o[1] + '</button>';
    }).join('') + '</div>';
  }

  function renderProps() {
    var el = selected();
    if (!el) { renderPage(); return; }
    var h = '';
    var typeName = {text: '🅣 Matn', image: '🖼 Rasm', rect: '▭ To\'rtburchak', ellipse: '◯ Doira', line: '━ Chiziq'}[el.type];
    h += '<div class="dz-ptitle"><b>' + typeName + '</b><span class="muted small">' + esc(el.name || '') + '</span></div>';
    if (el.locked) h += '<div class="alert info small">🔒 Qulflangan. Tahrirlash uchun qulfni oching.</div>';
    if (el.type === 'text') {
      h += '<div class="field"><label>Matn</label><textarea id="p-text" data-k="text" rows="3">' + esc(el.text) + '</textarea>' +
        '<div class="chips mt-s">' + DATA.placeholders.map(function (p) {
          return '<button type="button" class="chip" data-ins="' + p.key + '" title="' + esc(p.label) + ' — ' + esc(p.sample) + '">' + esc(p.label) + '</button>';
        }).join('') + '</div><div class="help">Tugma bosilsa, kursor turgan joyga qo\'shiladi. Har o\'quvchiga o\'z ma\'lumoti chiqadi.</div></div>';
      var opts = {}, f = fontEntry(el.font);
      Object.keys(FONTS.fonts).forEach(function (k) { var c = FONTS.fonts[k].cat; (opts[c] = opts[c] || []).push(k); });
      h += '<div class="field"><label>Shrift</label><select data-k="font">' + Object.keys(opts).map(function (c) {
        return '<optgroup label="' + esc(c) + '">' + opts[c].map(function (k) {
          return '<option value="' + k + '"' + (k === el.font ? ' selected' : '') + '>' + esc(FONTS.fonts[k].name) + '</option>';
        }).join('') + '</optgroup>';
      }).join('') + '</select></div>';
      h += '<div class="grid g2">' + num('O\'lcham', 'pt', sizeToPt(el.size), 0.5, 1, 400, 'pt') + color('Rang', 'color', el.color) + '</div>';
      h += '<div class="dz-row">' + check('<b>Qalin</b>', 'bold', el.bold, !f.has_bold) + check('KATTA HARF', 'upper', el.upper) + '</div>';
      h += '<div class="dz-row">' + seg('align', el.align, [['left', '⯇', 'Chapga'], ['center', '≡', 'Markazga'], ['right', '⯈', 'O\'ngga']]) +
        seg('valign', el.valign, [['top', '⤒', 'Tepaga'], ['middle', '↕', 'O\'rtaga'], ['bottom', '⤓', 'Pastga']]) + '</div>';
      h += '<div class="grid g2">' + num('Qator oralig\'i', 'lh', el.lh, 0.05, 0.6, 4) + num('Harf oralig\'i', 'ls', el.ls, 0.01, -0.2, 2, 'em') + '</div>';
      h += '<div class="dz-row">' + check('Qatorlarga bo\'lish', 'wrap', el.wrap !== false) + check('Sig\'masa kichraytirish', 'fit', el.fit !== false) + '</div>';
      h += '<div class="help mb">Uzun ism sig\'masa — shrift avtomatik kichrayadi (ramkani kattalashtirsangiz, kamroq kichrayadi).</div>';
      h += '<details class="dz-more"' + (el.shadow ? ' open' : '') + '><summary>' + check('Soya', 'shadow_on', !!el.shadow) + '</summary>';
      if (el.shadow) h += '<div class="grid g2">' + color('Rang', 'shadow.color', el.shadow.color) +
        num('Shaffoflik', 'shadow.alpha', alphaOf(el.shadow.color) * 100, 5, 0, 100, '%') +
        num('Xiralik', 'shadow.blur', pctToMM(el.shadow.blur, 0), 0.1, 0, 50, 'mm') +
        num('Siljish X', 'shadow.dx', pctToMM(el.shadow.dx, 0), 0.1, -50, 50, 'mm') +
        num('Siljish Y', 'shadow.dy', pctToMM(el.shadow.dy, 0), 0.1, -50, 50, 'mm') + '</div>';
      h += '</details>';
      h += '<details class="dz-more"' + (el.stroke ? ' open' : '') + '><summary>' + check('Kontur (harf atrofida chiziq)', 'stroke_on', !!el.stroke) + '</summary>';
      if (el.stroke) h += '<div class="grid g2">' + color('Rang', 'stroke.color', el.stroke.color) +
        num('Qalinlik', 'stroke.w', pctToMM(el.stroke.w, 0), 0.05, 0.05, 20, 'mm') + '</div>';
      h += '</details>';
    } else if (el.type === 'image') {
      h += '<div class="field"><label class="btn sm dz-file">🔁 Rasmni almashtirish<input type="file" accept="image/png,image/jpeg,image/webp" id="p-replace"></label></div>';
      h += '<div class="field"><label>Joylashuv</label><select data-k="fit"><option value="contain"' + (el.fit === 'contain' ? ' selected' : '') + '>Sig\'dirish (butun rasm)</option>' +
        '<option value="cover"' + (el.fit === 'cover' ? ' selected' : '') + '>To\'ldirish (qirqiladi)</option><option value="stretch"' + (el.fit === 'stretch' ? ' selected' : '') + '>Cho\'zish</option></select></div>';
      h += num('Burchak yumaloqligi', 'radius', el.radius || 0, 1, 0, 100, '%');
      h += '<div class="help mb">Logo, muhr va imzo uchun foni shaffof PNG yuklang.</div>';
    } else if (el.type === 'line') {
      h += '<div class="grid g2">' + color('Rang', 'stroke', el.stroke) + num('Qalinlik', 'sw', pctToMM(el.sw, 0), 0.05, 0.05, 50, 'mm') + '</div>';
    } else {
      h += '<div class="grid g2"><div>' + color('To\'ldirish', 'fill', el.fill || '#ffffff') + check('Bo\'sh (rangsiz)', 'fill_none', !el.fill) + '</div>' +
        '<div>' + color('Chiziq rangi', 'stroke', el.stroke || '#111827') + '</div></div>';
      h += '<div class="grid g2">' + num('Chiziq qalinligi', 'sw', el.stroke ? pctToMM(el.sw, 0) : 0, 0.05, 0, 50, 'mm') +
        (el.type === 'rect' ? num('Yumaloqlik', 'radius', el.radius || 0, 1, 0, 100, '%') : '') + '</div>';
    }
    // Umumiy: joylashuv
    h += '<h3 class="mt">Joylashuv</h3><div class="grid g2">' +
      num('X', 'x', pctToMM(el.x, 0), 0.5, undefined, undefined, 'mm') + num('Y', 'y', pctToMM(el.y, 1), 0.5, undefined, undefined, 'mm') +
      num('Kenglik', 'w', pctToMM(el.w, 0), 0.5, 1, undefined, 'mm') + num('Balandlik', 'h', pctToMM(el.h, 1), 0.5, 1, undefined, 'mm') +
      num('Burish', 'rot', el.rot || 0, 1, -180, 180, '°') + num('Shaffoflik', 'opacity', (el.opacity === undefined ? 1 : el.opacity) * 100, 5, 0, 100, '%') + '</div>';
    h += '<div class="btns dz-actions">' +
      '<button type="button" class="btn sm" data-act="center-h" title="Gorizontal markazga">↔ Markaz</button>' +
      '<button type="button" class="btn sm" data-act="center-v" title="Vertikal markazga">↕ Markaz</button>' +
      '<button type="button" class="btn sm" data-act="front" title="Eng oldinga">⤒ Oldinga</button>' +
      '<button type="button" class="btn sm" data-act="back" title="Eng orqaga">⤓ Orqaga</button>' +
      '<button type="button" class="btn sm" data-act="dup" title="Ctrl+D">⧉ Nusxa</button>' +
      '<button type="button" class="btn sm" data-act="lock">' + (el.locked ? '🔓 Qulfni ochish' : '🔒 Qulflash') + '</button>' +
      '<button type="button" class="btn sm danger" data-act="del" title="Delete">🗑 O\'chirish</button></div>';
    h += '<div class="field mt"><label>Nomi (qatlamlarda)</label><input type="text" data-k="name" value="' + esc(el.name || '') + '" maxlength="60"></div>';
    props.innerHTML = h;
    if (el.locked) props.querySelectorAll('input,select,textarea').forEach(function (i) { if (i.getAttribute('data-k') !== 'name') i.disabled = true; });
    var rep = $('#p-replace');
    if (rep) rep.addEventListener('change', function () {
      if (!this.files[0]) return;
      uploadAsset(this.files[0]).then(function (j) { el.asset = j.name; schedule(); touch(); });
    });
  }

  function renderPage() {
    var p = design.page, bg = design.bg, mm = pageMM();
    var h = '<div class="dz-ptitle"><b>📄 Sahifa va fon</b><span class="muted small">' + Math.round(mm[0]) + '×' + Math.round(mm[1]) + ' mm · ' + W + '×' + H + ' px</span></div>';
    h += '<div class="field"><label>O\'lcham</label><select data-pk="preset">' + DATA.pages.map(function (o) {
      return '<option value="' + o[0] + '"' + (p.preset === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('') + '<option value="custom"' + (p.preset === 'custom' ? ' selected' : '') + '>Boshqa (mm)</option>' +
      '<option value="image"' + (p.preset === 'image' ? ' selected' : '') + '>Fon rasmi o\'lchamida</option></select></div>';
    if (p.preset === 'custom') h += '<div class="grid g2">' + num('Kenglik', 'w_mm', p.w_mm || 297, 1, 50, 1000, 'mm').replace('data-k', 'data-pk') +
      num('Balandlik', 'h_mm', p.h_mm || 210, 1, 50, 1000, 'mm').replace('data-k', 'data-pk') + '</div>';
    if (p.preset !== 'image') h += '<div class="field"><label>Sifat</label><select data-pk="dpi">' + [[150, 'Standart (150 dpi) — tez'], [200, 'Yaxshi (200 dpi)'], [300, 'Chop etish uchun eng yuqori (300 dpi)']].map(function (o) {
      return '<option value="' + o[0] + '"' + (p.dpi === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
    }).join('') + '</select><div class="help">Joylashuv o\'zgarmaydi — faqat aniqlik. 300 dpi fayllar kattaroq bo\'ladi.</div></div>';
    h += '<h3 class="mt">Fon</h3>' + seg('bgtype', bg.type, [['color', '■ Rang', 'Bir rang'], ['gradient', '◩ Gradient', 'Ikki rang o\'tishi'], ['image', '🖼 Rasm', 'Rasm (Canva\'da tayyorlangan bo\'lsa ham)']]);
    if (bg.type === 'color') h += '<div class="grid g2 mt-s">' + color('Rang', 'bg.color', bg.color).replace('data-k', 'data-bk') + '</div>';
    if (bg.type === 'gradient') h += '<div class="grid g2 mt-s">' + color('1-rang', 'bg.color', bg.color).replace('data-k', 'data-bk') +
      color('2-rang', 'bg.color2', bg.color2).replace('data-k', 'data-bk') + num('Burchak', 'bg.angle', bg.angle, 5, -360, 360, '°').replace('data-k', 'data-bk') + '</div>';
    if (bg.type === 'image') {
      h += '<div class="field mt-s"><label class="btn sm dz-file">⬆️ ' + (bg.image ? 'Boshqa rasm' : 'Rasm yuklash') + '<input type="file" accept="image/png,image/jpeg,image/webp" id="p-bg"></label></div>';
      if (bg.image) {
        h += '<div class="field"><label>Joylashuv</label><select data-bk="bg.fit"><option value="cover"' + (bg.fit === 'cover' ? ' selected' : '') + '>To\'ldirish</option>' +
          '<option value="contain"' + (bg.fit === 'contain' ? ' selected' : '') + '>Sig\'dirish</option><option value="stretch"' + (bg.fit === 'stretch' ? ' selected' : '') + '>Cho\'zish</option></select></div>';
        h += color('Bo\'sh joy rangi', 'bg.color', bg.color).replace('data-k', 'data-bk');
        h += '<div class="btns"><button type="button" class="btn sm" data-act="fit-page">📐 Sahifani rasm o\'lchamiga moslash</button></div>';
      }
      h += '<div class="help">Canva\'da tayyor dizayn qilgan bo\'lsangiz: uni PNG qilib yuklang, so\'ng «Sahifani rasm o\'lchamiga moslash»ni bosing va faqat ism, ball kabi o\'zgaruvchilarni joylang.</div>';
    }
    h += '<div class="help mt">Elementni tanlash uchun ustiga bosing. Sichqoncha bilan suring, burchakdan cho\'zing, tepadagi doiracha bilan buring. ' +
      'Alt — yopishmasdan surish, Shift — burchakni 15° qadam bilan burish. Ctrl+Z orqaga, Ctrl+D nusxa, Delete o\'chirish, Ctrl+S saqlash.</div>';
    props.innerHTML = h;
    var inp = $('#p-bg');
    if (inp) inp.addEventListener('change', function () {
      if (!this.files[0]) return;
      uploadAsset(this.files[0]).then(function (j) {
        bg.image = j.name; bg.type = 'image';
        var ask = Math.abs(j.w / j.h - W / H) > 0.02;
        if (ask && confirm('Rasm nisbati sahifadan farq qiladi. Sahifani rasm o\'lchamiga moslaymi?')) fitPageToImage(j.w, j.h);
        schedule(); renderProps(); touch();
      });
    });
  }
  function fitPageToImage(iw, ih) {
    // Uzun tomoni A4 (297 mm) bo'ladigan qilib DPI tanlaymiz — chop etilganda A4 o'lchamda chiqadi
    var long = Math.max(iw, ih), k = Math.min(1, 3508 / long);
    design.page = {preset: 'image', w: Math.round(iw * k), h: Math.round(ih * k), dpi: Math.round(clamp(Math.max(iw, ih) * k * 25.4 / 297, 50, 600))};
    design.bg.fit = 'stretch';
    var ps = pageSize(design.page); W = ps[0]; H = ps[1];
    rebuild();
  }

  function renderLayers() {
    var h = '';
    for (var i = design.els.length - 1; i >= 0; i--) {
      var el = design.els[i];
      var label = el.name || (el.type === 'text' ? (el.text || '').slice(0, 40) : {image: 'Rasm', rect: 'To\'rtburchak', ellipse: 'Doira', line: 'Chiziq'}[el.type]);
      var ico = {text: '🅣', image: '🖼', rect: '▭', ellipse: '◯', line: '━'}[el.type];
      h += '<div class="dz-layer' + (el.id === sel ? ' on' : '') + (el.hidden ? ' off' : '') + '" data-layer="' + el.id + '">' +
        '<span class="ico">' + ico + '</span><span class="nm">' + esc(label) + '</span>' +
        '<button type="button" data-lact="up" title="Yuqoriga">▲</button><button type="button" data-lact="down" title="Pastga">▼</button>' +
        '<button type="button" data-lact="hide" title="Ko\'rsatish/yashirish">' + (el.hidden ? '🚫' : '👁') + '</button>' +
        '<button type="button" data-lact="lock" title="Qulf">' + (el.locked ? '🔒' : '🔓') + '</button></div>';
    }
    layers.innerHTML = h || '<div class="muted small">Elementlar yo\'q</div>';
  }

  function syncGeometry() {
    var el = selected(); if (!el) return;
    var map = {x: pctToMM(el.x, 0), y: pctToMM(el.y, 1), w: pctToMM(el.w, 0), h: pctToMM(el.h, 1), rot: el.rot || 0};
    if (el.type === 'text') map.pt = sizeToPt(el.size);
    Object.keys(map).forEach(function (k) {
      var i = props.querySelector('[data-k="' + k + '"]');
      if (i && document.activeElement !== i) i.value = r2(map[k]);
    });
  }

  // Panel kiritishlari
  props.addEventListener('input', onProp);
  props.addEventListener('change', onProp);
  function onProp(e) {
    var t = e.target, k = t.getAttribute('data-k'), pk = t.getAttribute('data-pk'), bk = t.getAttribute('data-bk');
    if (pk) { pageProp(pk, t, e.type); return; }
    if (bk) { var key = bk.split('.')[1], v = t.type === 'number' ? parseFloat(t.value) : t.value;
      if (t.type === 'number' && isNaN(v)) return;
      design.bg[key] = v; schedule(); touch(); return; }
    var el = selected(); if (!el || !k) return;
    var val = t.type === 'checkbox' ? t.checked : (t.type === 'number' ? parseFloat(t.value) : t.value);
    if (t.type === 'number' && isNaN(val)) return;
    var rebuildPanel = false;
    switch (k) {
      case 'x': el.x = mmToPct(val, 0); break;
      case 'y': el.y = mmToPct(val, 1); break;
      case 'w': el.w = Math.max(0.2, mmToPct(val, 0)); break;
      case 'h': el.h = Math.max(0.2, mmToPct(val, 1)); break;
      case 'pt': el.size = clamp(ptToSize(val), 0.2, 40); break;
      case 'opacity': el.opacity = clamp(val / 100, 0, 1); break;
      case 'sw': el.sw = clamp(mmToPct(val, 0), 0, 10); break;
      case 'fill_none': el.fill = val ? '' : '#ffffff'; rebuildPanel = true; break;
      case 'fill': el.fill = val; break;
      case 'stroke': el.stroke = val; if (el.type !== 'line' && !(el.sw > 0)) { el.sw = 0.2; rebuildPanel = true; } break;
      case 'shadow_on': el.shadow = val ? {color: '#00000066', blur: 0.3, dx: 0.15, dy: 0.15} : null; rebuildPanel = true; break;
      case 'stroke_on': el.stroke = val ? {color: '#ffffff', w: 0.1} : null; rebuildPanel = true; break;
      case 'shadow.color': el.shadow.color = withAlpha(val, alphaOf(el.shadow.color)); break;
      case 'shadow.alpha': el.shadow.color = withAlpha(el.shadow.color, val / 100); break;
      case 'shadow.blur': el.shadow.blur = clamp(mmToPct(val, 0), 0, 5); break;
      case 'shadow.dx': el.shadow.dx = clamp(mmToPct(val, 0), -5, 5); break;
      case 'shadow.dy': el.shadow.dy = clamp(mmToPct(val, 0), -5, 5); break;
      case 'stroke.color': el.stroke.color = val; break;
      case 'stroke.w': el.stroke.w = clamp(mmToPct(val, 0), 0.01, 3); break;
      case 'font':
        el.font = val;
        if (el.bold && !fontEntry(val).has_bold) el.bold = false;
        ensureFont(val, el.bold); rebuildPanel = e.type === 'change'; break;
      case 'bold': el.bold = val; ensureFont(el.font, val); break;
      case 'name': el.name = val; renderLayers(); break;
      default: el[k] = val;
    }
    if (k === 'text') renderLayers();
    schedule(); touch();
    if (rebuildPanel) renderProps();
  }
  function pageProp(pk, t, type) {
    if (type === 'input') return;   // sahifa o'lchami faqat «change»da (yozib turganda panel qayta chizilmasin)
    var p = design.page, v = t.type === 'number' ? parseFloat(t.value) : t.value;
    if (t.type === 'number' && isNaN(v)) return;
    if (pk === 'preset') {
      if (v === 'image') {
        var sz = design.bg.image && ASSET_SIZE[design.bg.image];
        if (!sz || !sz[0]) { alert('Avval fon rasmini yuklang (Fon → Rasm).'); renderProps(); return; }
        fitPageToImage(sz[0], sz[1]); touch(); return;
      }
      p.preset = v; p.dpi = [150, 200, 300].indexOf(p.dpi) >= 0 ? p.dpi : 150;
      if (v === 'custom') { p.w_mm = p.w_mm || Math.round(pageMM()[0]); p.h_mm = p.h_mm || Math.round(pageMM()[1]); }
    } else if (pk === 'dpi') p.dpi = parseInt(v, 10);
    else p[pk] = v;
    var ps = pageSize(p); W = ps[0]; H = ps[1]; p.w = W; p.h = H;
    rebuild(); touch();
  }

  // Tugmalar (panel, segment, chip, qatlam)
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-act],[data-seg],[data-ins],[data-var],[data-preset],[data-lact],[data-layer],[data-tab]');
    if (!b) { closeMenus(); return; }
    if (b.hasAttribute('data-tab')) {
      e.preventDefault();
      document.querySelectorAll('[data-tab]').forEach(function (x) { x.classList.toggle('active', x === b); });
      document.querySelectorAll('[data-pane]').forEach(function (x) { x.classList.toggle('hidden', x.getAttribute('data-pane') !== b.getAttribute('data-tab')); });
      return;
    }
    if (b.hasAttribute('data-seg')) {
      var key = b.getAttribute('data-seg'), v = b.getAttribute('data-v');
      if (key === 'bgtype') { design.bg.type = v; if (v === 'gradient' && !design.bg.color2) design.bg.color2 = '#dbeafe'; }
      else { var el = selected(); if (!el || el.locked) return; el[key] = v; }
      schedule(); touch(); renderProps(); return;
    }
    if (b.hasAttribute('data-ins')) { var ta = $('#p-text'); if (ta && !ta.disabled) insertAtCursor(ta, '{' + b.getAttribute('data-ins') + '}'); return; }
    if (b.hasAttribute('data-var')) { closeMenus(); addVar(b.getAttribute('data-var')); return; }
    if (b.hasAttribute('data-preset')) { closeMenus(); applyPreset(b.getAttribute('data-preset')); return; }
    if (b.hasAttribute('data-lact')) {
      var id = b.closest('[data-layer]').getAttribute('data-layer'), l = byId(id), a = b.getAttribute('data-lact');
      if (a === 'hide') l.hidden = !l.hidden; else if (a === 'lock') l.locked = !l.locked;
      else { sel = id; reorder(a === 'up' ? 1 : -1); }
      schedule(); renderLayers(); if (sel === id) renderProps(); touch(); return;
    }
    if (b.hasAttribute('data-layer')) { select(b.getAttribute('data-layer')); return; }
    var act = b.getAttribute('data-act');
    var A = {
      'add-text': function () { add(defaults('text')); },
      'add-rect': function () { add(defaults('rect')); },
      'add-ellipse': function () { add(defaults('ellipse')); },
      'add-line': function () { add(defaults('line')); },
      'undo': undo, 'redo': redo,
      'zoom-in': function () { setZoom(zoom * 1.25); }, 'zoom-out': function () { setZoom(zoom / 1.25); },
      'zoom-fit': function () { fitView(true); },
      'save': function () { dirty = true; save(); },
      'preview': function () { preview(''); },
      'preview-pid': function () { preview($('#dz-pid').value.trim()); },
      'close-preview': function () { $('#dz-preview').classList.add('hidden'); },
      'toggle-vars': function () { toggleMenu('#dz-vars'); },
      'toggle-presets': function () { toggleMenu('#dz-presets'); },
      'center-h': function () { align('h'); }, 'center-v': function () { align('v'); },
      'front': function () { reorder('top'); }, 'back': function () { reorder('bottom'); },
      'dup': function () { duplicate(); }, 'del': remove,
      'lock': function () { var el = selected(); if (el) { el.locked = !el.locked; schedule(); renderProps(); renderLayers(); touch(); } },
      'fit-page': function () { var s = ASSET_SIZE[design.bg.image]; if (s && s[0]) { fitPageToImage(s[0], s[1]); touch(); } }
    };
    if (A[act]) { e.preventDefault(); A[act](); }
  });
  function toggleMenu(s) { var m = $(s), open = !m.classList.contains('open'); closeMenus(); if (open) m.classList.add('open'); }
  function closeMenus() { document.querySelectorAll('.dz-menu.open').forEach(function (m) { m.classList.remove('open'); }); }

  function applyPreset(key) {
    if (!confirm('Joriy dizayn tanlangan tayyor dizayn bilan almashtiriladi. Davom etaymi? (Ctrl+Z bilan qaytarish mumkin)')) return;
    fetch(DATA.presetUrl + key + '.json', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (d) {
      design = d; sel = null;
      var ps = pageSize(design.page); W = ps[0]; H = ps[1];
      rebuild(); touch();
    });
  }
  function preview(code) {
    save().then(function () {
      var url = '/admin/certificates/' + TID + '/preview.png?t=' + Date.now() + (code ? '&code=' + encodeURIComponent(code) : '');
      $('#dz-preview-img').src = url;
      $('#dz-preview').classList.remove('hidden');
      $('#dz-pdf').href = '/admin/certificates/' + TID + '/sample.pdf' + (code ? '?code=' + encodeURIComponent(code) : '');
    });
  }

  // Klaviatura
  document.addEventListener('keydown', function (e) {
    var tag = (document.activeElement && document.activeElement.tagName) || '';
    var typing = /INPUT|TEXTAREA|SELECT/.test(tag);
    var mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); dirty = true; save(); return; }
    if (typing) return;
    if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
    var el = selected();
    if (mod && e.key.toLowerCase() === 'c' && el) { clipboard = JSON.parse(JSON.stringify(el)); return; }
    if (mod && e.key.toLowerCase() === 'v' && clipboard) { e.preventDefault(); duplicate(clipboard); return; }
    if (!el) return;
    if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicate(); return; }
    if (e.key === 'Escape') { select(null); return; }
    if (el.locked) return;
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(); return; }
    var step = e.shiftKey ? 1 : 0.1, moved = true;
    if (e.key === 'ArrowLeft') el.x -= step; else if (e.key === 'ArrowRight') el.x += step;
    else if (e.key === 'ArrowUp') el.y -= step * W / H; else if (e.key === 'ArrowDown') el.y += step * W / H;
    else moved = false;
    if (moved) { e.preventDefault(); schedule(); syncGeometry(); touch(); }
  });

  // Ko'lam (zoom)
  function setZoom(z) { zoom = clamp(z, 0.05, 4); applyZoom(); schedule(); }
  function fitView(force) {
    var aw = Math.max(200, wrap.clientWidth - 32), ah = Math.max(200, window.innerHeight - wrap.getBoundingClientRect().top - 40);
    fitZoom = Math.min(aw / W, Math.max(ah, 360) / H);
    if (force || !zoom || zoom === 1) zoom = fitZoom;
    applyZoom();
  }
  window.addEventListener('resize', function () { var rel = zoom / fitZoom; fitView(false); zoom = fitZoom * rel; applyZoom(); schedule(); });

  // Asboblar paneli: rasm, namuna
  $('#dz-img-input').addEventListener('change', function () { if (this.files[0]) addImage(this.files[0]); this.value = ''; });
  $('#dz-sample').addEventListener('change', function () { showSample = this.checked; schedule(); });
  $('#dz-sample-name').addEventListener('input', function () { sampleName = this.value.trim(); schedule(); });

  // ------------------------------------------------------------------ ishga tushirish
  setStatus('Shriftlar yuklanmoqda…');
  fetch('/admin/certificates/fonts.json', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
    FONTS = j;
    j.charset.forEach(function (c, i) { CHARIDX.set(c, i); });
    var ps = pageSize(design.page); W = ps[0]; H = ps[1];
    var loads = [];
    design.els.forEach(function (el) {
      if (el.type === 'text') loads.push(ensureFont(el.font, el.bold));
      if (el.type === 'image') loadAssetSize(el.asset);
    });
    if (design.bg.image) loadAssetSize(design.bg.image);
    buildStage(); fitView(true); renderProps(); renderLayers();
    Promise.all(loads).then(function () { draw(); setStatus(''); });
    draw();
  }).catch(function (e) { setStatus('⚠️ Shriftlarni yuklab bo\'lmadi: ' + e.message, 'err-text'); });
})();
