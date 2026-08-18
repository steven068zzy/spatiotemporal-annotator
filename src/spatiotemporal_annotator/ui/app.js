'use strict';
// The browser holds no authoritative state. Every edit is posted, the server rewrites the
// clip document, and the rows it returns replace the ones held here. A reload therefore
// always shows what is on disk. Local repaints exist only so a drag feels live; the
// tested copy of every rule is in core.py, and where a rule is mirrored here it says so.

const $ = id => document.getElementById(id);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const MISSED = 'm', NODATA = '-';

let CFG = null;                 // /api/config
let SCOL = {}, SNAME = {}, PAINT = {};   // state key -> colour, name, hotkey map
let clips = [], curClip = -1, doc = null;
let imgs = [], cur = 0, sel = null, hoverBird = null;
let cands = [], candPos = 0;
let playing = false, timer = null, fps = 5;
let showDismissed = !!localStorage.getItem('staShowDismissed');
let held = null, undoStack = [], saveTimer = null, savePending = null;
let drag = null, hotBound = null;

// ---- helpers ----------------------------------------------------------------------
const dismissed = b => b.status === 'merged' || b.status === 'discarded';
const requireZone = () => !!(doc && doc.zones && doc.zones.length);
const countable = b => !dismissed(b) && (!requireZone() || !!b.zone);
const live = () => doc.individuals.filter(countable);
const ignored = () => doc.individuals.filter(
  b => !dismissed(b) && requireZone() && !b.zone);
const byId = id => doc.individuals.find(b => b.individual_id === id);
const POST = (url, body) => fetch(url, {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

let toastTimer = null;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

// Mirrors core.overlay(). Countable rows first, and a rectangle already claimed by an
// earlier entry is dropped, so a merged twin never double-strokes the row that took it.
function overlay(frame) {
  const rank = b => countable(b) ? 0 : (b.status !== 'merged' ? 1 : 2);
  const out = [], seen = new Set();
  [...doc.individuals].sort((p, q) => rank(p) - rank(q)).forEach(b => {
    const box = (b.boxes || [])[frame];
    if (!box) return;
    const k = box.join(',');
    if (seen.has(k)) return;
    seen.add(k);
    out.push({bird: b, box, kind: countable(b) ? 'individual' : 'orphan'});
  });
  return out;
}

function activeTrack(segments, f) {          // mirrors core.active_track
  let tid = null;
  for (const s of segments) { if (s.from <= f) tid = s.track_id; else break; }
  return tid;
}

function observableCount(fs) {
  let n = 0;
  for (const c of fs) if (CFG.states.observable.includes(c)) n++;
  return n;
}
function primaryFrac(fs) {
  const n = observableCount(fs);
  if (!n) return 0;
  let a = 0;
  for (const c of fs) if (c === CFG.states.active) a++;
  return a / n;
}

// ---- boot -------------------------------------------------------------------------
async function init() {
  CFG = await (await fetch('/api/config')).json();
  document.title = CFG.name ? CFG.name + ' — annotator' : 'Spatiotemporal annotator';
  $('projName').textContent = CFG.name || 'Spatiotemporal annotator';
  CFG.states.states.forEach(s => { SCOL[s.key] = s.color; SNAME[s.key] = s.name;
                                   PAINT[s.key.toLowerCase()] = s.key; });
  SCOL[MISSED] = css('--miss'); SNAME[MISSED] = 'missed'; PAINT['m'] = MISSED;
  SCOL[NODATA] = css('--nodata'); SNAME[NODATA] = 'no data';
  fps = CFG.playback_fps || 5;
  buildSpeeds(); buildLegend(); buildKeyHelp(); fillSettings(CFG.settings);
  await loadClips();
  loadStats();
}

function buildSpeeds() {
  const s = $('spd');
  s.innerHTML = '';
  const opts = (CFG.playback_speeds || [3, 5, 8]).slice();
  if (!opts.includes(fps)) opts.push(fps);
  opts.sort((a, b) => a - b).forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v + ' fps'; o.selected = (v === fps);
    s.appendChild(o);
  });
}

function buildLegend() {
  const parts = CFG.states.states.map(s =>
    `<span><span class=dot style="background:${s.color}"></span>${s.name}` +
    ` <kbd>${s.key.toUpperCase()}</kbd></span>`);
  parts.push(`<span><span class=dot style="background:${SCOL[MISSED]}"></span>missed <kbd>M</kbd></span>`);
  parts.push(`<span><span class=dot style="background:${SCOL[NODATA]}"></span>no data` +
             ` &mdash; hold <b>M</b> to claim it</span>`);
  parts.push(`<span><span class=dot style="background:var(--orphan)"></span>` +
             `unclaimed box &mdash; click to take it</span>`);
  parts.push(`<span id=dismissToggle class=tg></span>`);
  parts.push(`<span id=gridNote></span>`);
  $('legend').innerHTML = parts.join('');
  $('dismissToggle').onclick = () => setShowDismissed(!showDismissed);
  syncDismissToggle();
}

function syncDismissToggle() {
  const el = $('dismissToggle');
  if (el) el.textContent = showDismissed ? 'hide dismissed rows' : 'show dismissed rows';
}

function buildKeyHelp() {
  const paint = CFG.states.states
    .map(s => `hold <b>${s.key.toUpperCase()}</b> ${s.name}`).join(' &middot; ');
  const base = CFG.states.states.find(s => s.baseline);
  $('keyHelp').innerHTML =
    `every frame starts <b>${base ? base.name : 'baseline'}</b> &middot; ${paint} &middot; ` +
    `hold <b>M</b> missed, including past the ends of the track<br>` +
    `<b>C</b> confirm this individual + next &middot; <b>U</b> back to unannotated &middot; ` +
    `<b>click the &#10003;/&#9675;</b> to toggle either<br>` +
    `<b>X</b> or the row's <b>&#10005;</b>: not an individual, discard the row &middot; ` +
    `restore it from <b>show dismissed rows</b><br>` +
    `<b>drag the timeline</b> to scrub &middot; <b>drag a colour boundary</b> to move it ` +
    `&middot; <b>click a box</b> re-anchor<br>` +
    `<b>Tab</b> cycle overlapping boxes &middot; <b>&uarr;&darr;</b> individual &middot; ` +
    `<b>Space &larr;&rarr;</b> playback &middot; <b>Z</b> undo`;
}

// ---- clip queue -------------------------------------------------------------------
async function loadClips() {
  clips = await (await fetch('/api/clips')).json();
  if (!clips.length) {
    $('status').textContent = 'no clips yet';
    $('clipProg').textContent = '—';
    toast('This project has no clips. Use + Add video.');
    return;
  }
  buildClipList(0);
  const first = clips.findIndex(c => !c.complete && !c.skipped);
  await loadClip(first < 0 ? 0 : first);
}

function clipLabel(c) {
  const tags = Object.entries(c.tags || {}).map(([k, v]) => `${k}=${v}`).join(' ');
  const mark = c.skipped ? '⊘ ' : (c.complete ? '✓ ' : '');
  return `${mark}${c.id}${tags ? '  ·  ' + tags : ''}  ·  ${c.done}/${c.total}`;
}

function buildClipList(selIdx) {
  const s = $('clipSel');
  s.innerHTML = '';
  const open = document.createElement('optgroup'); open.label = 'to do';
  const done = document.createElement('optgroup'); done.label = 'finished or skipped';
  clips.forEach((c, i) => {
    const o = document.createElement('option');
    o.value = i; o.textContent = clipLabel(c);
    ((c.complete || c.skipped) ? done : open).appendChild(o);
  });
  if (open.children.length) s.appendChild(open);
  if (done.children.length) s.appendChild(done);
  s.value = selIdx;
}

$('clipSel').onchange = e => loadClip(+e.target.value);

function nextOpenClip() {
  const order = [...$('clipSel').options].map(o => +o.value);
  const at = order.indexOf(curClip);
  for (let k = 1; k <= order.length; k++) {
    const i = order[(at + k) % order.length];
    if (!clips[i].complete && !clips[i].skipped) return i;
  }
  return -1;
}

function syncClipEntry() {
  const c = clips[curClip];
  if (!c) return;
  const l = live();
  c.done = l.filter(b => b.status === 'confirmed').length;
  c.total = l.length;
  c.complete = !!doc.complete;
  const o = [...$('clipSel').options].find(o => +o.value === curClip);
  if (o) o.textContent = clipLabel(c);
}

async function loadClip(i) {
  stopPlay();
  curClip = i;
  const id = clips[i].id;
  $('status').textContent = 'loading ' + id + '…';
  const r = await (await fetch('/api/clip/' + encodeURIComponent(id))).json();
  if (r.err) { $('status').textContent = r.err; return toast(r.err); }
  doc = r.doc;
  clips[i].opened = true;
  undoStack = [];
  imgs = new Array(doc.n_frames);
  for (let f = 0; f < doc.n_frames; f++) {
    const im = new Image();
    im.src = `/frame/${encodeURIComponent(doc.clip)}/${String(f).padStart(5, '0')}.jpg`;
    im.onload = () => { if (f === cur) drawAll(); };
    imgs[f] = im;
  }
  cur = 0; sel = null; hoverBird = null;
  const l = live();
  const firstOpen = l.find(b => b.status === 'unseen') || l[0];
  if (firstOpen) selectBird(firstOpen.individual_id);
  $('status').textContent = doc.complete ? 'complete' : 'in progress';
  buildClipList(i);
  sizeStage(); buildGrid(); drawAll();
}

// ---- progress panel ---------------------------------------------------------------
async function loadStats() {
  try {
    renderStats(await (await fetch('/api/stats')).json());
  } catch (e) {
    $('statsBody').textContent = 'progress unavailable';
  }
}

const DONE = () => css('--ok'), OPEN = () => css('--focal');

// Bars are scaled within their own chart, so a short bar means "few clips here", never
// "few of them finished". The fill inside a bar is the finished share.
function chart(title, rows) {
  const max = Math.max(1, ...rows.map(r => r.total));
  return `<div class=chart><div class=ct>${title}</div>` + rows.map(r =>
    `<div class=brow><div class=bl>${r.label}</div>` +
    `<div class=btrack style="width:${(100 * r.total / max).toFixed(1)}%">` +
      `<i style="width:${r.total ? (100 * r.done / r.total).toFixed(1) : 0}%;` +
      `background:${r.color || DONE()}"></i></div>` +
    `<div class=bv>${r.text}</div></div>`).join('') + '</div>';
}

function renderStats(s) {
  const c = s.clips, tot = c.complete + c.started + c.untouched || 1;
  const seg = (n, col) => `<i style="width:${(100 * n / tot).toFixed(2)}%;background:${col}"></i>`;
  const grp = g => ({label: g.key, total: g.complete + g.started, done: g.complete,
                     text: `${g.complete}/${g.complete + g.started}`});
  const charts = [];
  if (s.zone.length > 1 || (s.zone.length === 1 && s.zone[0].key !== '?')) {
    const mx = Math.max(1, ...s.zone.map(z => z.n));
    charts.push(chart('by zone · individuals', s.zone.map(z =>
      ({label: z.key, total: z.n, done: z.n, text: String(z.n),
        color: DONE()}))));
    void mx;
  }
  (s.groups || []).forEach(g =>
    charts.push(chart('by ' + g.key + ' · clips', g.rows.map(grp))));
  const stateRows = s.state_frames.filter(r => r.n > 0);
  const smax = Math.max(1, ...stateRows.map(r => r.n));
  if (stateRows.length) {
    charts.push(chart('frames by state', stateRows.map(r =>
      ({label: r.name, total: r.n, done: r.n, text: r.n.toLocaleString(),
        color: r.color}))));
    void smax;
  }

  $('statsBody').innerHTML =
    `<div class=sum>` +
      `<span class=sbar>${seg(c.complete, DONE())}${seg(c.started, OPEN())}</span>` +
      `<span class=slab><b>${c.complete}</b> complete · <b>${c.started}</b> started · ` +
      `<b>${c.untouched}</b> untouched of ${s.n_clips_on_disk}</span></div>` +
    `<div class=slab style="margin-bottom:9px"><b>${s.individuals_confirmed}</b> individuals ` +
      `confirmed · <b>${s.frames.individual.toLocaleString()}</b> labelled individual-frames · ` +
      `${SNAME[s.primary_state] || s.primary_state} fraction ` +
      `<b>${(s.primary_fraction * 100).toFixed(2)}%</b></div>` +
    '<div class=charts>' + charts.join('') + '</div>' +
    `<div class=snote><i style="background:${DONE()}"></i>complete` +
      `<i style="background:${OPEN()}"></i>started` +
      `<i style="background:#232c37"></i>untouched &middot; ` +
      `state counts cover complete clips only</div>`;
}

$('statsRefresh').onclick = e => { e.stopPropagation(); loadStats(); };
$('statsToggle').onclick = () => {
  const folded = $('stats').classList.toggle('folded');
  $('statsToggle').innerHTML = folded ? '&#9656;' : '&#9662;';
  localStorage.setItem('staStatsFolded', folded ? '1' : '');
};
if (localStorage.getItem('staStatsFolded')) $('statsToggle').onclick();

// ---- layout -----------------------------------------------------------------------
const GRID_MIN = 620, NARROW = 1280;

function sizeStage() {
  const stacked = innerWidth < NARROW;
  const availW = stacked ? innerWidth - 40 : innerWidth - GRID_MIN - 60;
  const availH = innerHeight - 55 - 15 - 34 - 200;   // header, ruler, track, strip+legend
  const maxS = CFG.display_max_scale || 1.6;
  const s = Math.max(1, Math.min(availW / doc.w, availH / doc.h, maxS));
  const W = Math.round(doc.w * s), H = Math.round(doc.h * s);
  const cv = $('cv');
  cv.width = doc.w; cv.height = doc.h;              // backing store stays native
  cv.style.width = W + 'px'; cv.style.height = H + 'px';
  $('stage').style.width = W + 'px'; $('stage').style.height = H + 'px';
  $('left').style.width = stacked ? 'auto' : W + 'px';
  for (const id of ['ruler', 'track']) {
    const c = $(id); c.width = W; c.style.width = W + 'px';
  }
}
addEventListener('resize', () => { if (doc) { sizeStage(); drawAll(); } });

// ---- grid -------------------------------------------------------------------------
function buildGrid() {
  const g = $('grid'); g.innerHTML = '';
  const zones = (doc.zones && doc.zones.length) ? doc.zones : [null];
  zones.forEach(zone => {
    const col = document.createElement('div'); col.className = 'col';
    const all = doc.individuals.filter(b => (b.zone || null) === zone);
    const rows = all.filter(b => showDismissed || !dismissed(b));
    const hid = all.length - rows.length;
    const head = zone ? `ZONE ${zone}` : 'ALL INDIVIDUALS';
    col.innerHTML = `<div class=colh>${head} &middot; ` +
      `${all.filter(b => !dismissed(b)).length}` +
      (hid ? ` <span style="color:#5a6470">&middot; ${hid} dismissed hidden</span>` : '') +
      '</div>';
    const box = document.createElement('div'); box.className = 'rows';
    rows.forEach(b => box.appendChild(makeRow(b)));
    col.appendChild(box); g.appendChild(col);
  });
}

function setShowDismissed(v) {
  showDismissed = v;
  localStorage.setItem('staShowDismissed', v ? '1' : '');
  syncDismissToggle();
  if (!doc) return;
  const s = sel && byId(sel);
  if (!v && s && dismissed(s)) {
    const l = live();
    selectBird(l.length ? l[0].individual_id : null);
  }
  buildGrid(); drawAll();
}

function makeRow(b) {
  const el = document.createElement('div');
  el.className = 'row' + (dismissed(b) ? ' dismissed' : '');
  el.dataset.ind = b.individual_id;
  el.innerHTML = `<canvas class=th width=19 height=19></canvas>
    <div class=lbl></div><canvas class=bar height=17></canvas><div class=pct></div>
    <div class=del title="not an individual: discard this row (X)">&#10005;</div>`;
  el.onclick = () => { selectBird(b.individual_id); drawAll(); };
  el.querySelector('.del').onclick = e => {
    e.stopPropagation(); discardRow(byId(b.individual_id), true);
  };
  el.querySelector('.lbl').onclick = e => {   // the glyph is its own control
    if (!e.target.classList.contains('gl')) return;
    e.stopPropagation();
    const row = byId(b.individual_id);
    if (!row || row.status === 'merged') return;
    if (row.status === 'discarded') return discardRow(row, false);
    selectBird(row.individual_id);
    row.status === 'confirmed' ? resetRow(row) : (drawAll(), confirmRow());
  };
  el.querySelector('.bar').onmousedown = e => {  // a seek strip, states are read-only here
    e.preventDefault(); e.stopPropagation();
    selectBird(b.individual_id); stopPlay();
    drag = {mode: 'scrub', bar: e.currentTarget};
    cur = barFrame(e); drawAll();
  };
  el.onmouseenter = () => { hoverBird = b.individual_id; drawVideo(); };
  el.onmouseleave = () => { hoverBird = null; drawVideo(); };
  return el;
}

// ---- drawing ----------------------------------------------------------------------
function drawAll() { drawVideo(); drawTimeline(); drawRows(); drawSelInfo(); updateHeader(); }

function drawVideo() {
  const cv = $('cv'), x = cv.getContext('2d');
  x.clearRect(0, 0, cv.width, cv.height);
  if (imgs[cur] && imgs[cur].complete) x.drawImage(imgs[cur], 0, 0);
  overlay(cur).forEach(({bird: b, box, kind}) => {
    const w = box[2] - box[0], h = box[3] - box[1];
    if (kind === 'orphan') {                   // a real individual no counted row owns
      x.lineWidth = 1.6; x.setLineDash([2, 3]); x.strokeStyle = css('--orphan');
      x.strokeRect(box[0], box[1], w, h);
      x.setLineDash([]); x.fillStyle = css('--orphan');
      x.fillRect(box[0], box[1] - 4, 9, 3);    // tick: unclaimed, click to take it
      return;
    }
    x.lineWidth = b.status === 'unseen' ? 1 : 1.6;
    x.setLineDash(b.status === 'unseen' ? [3, 3] : []);
    x.strokeStyle = b.status === 'unseen' ? '#7e8a99' : (SCOL[b.fstate[cur]] || '#7e8a99');
    x.strokeRect(box[0], box[1], w, h);
  });
  x.setLineDash([]);
  const h = hoverBird && byId(hoverBird);
  if (h && h.boxes[cur]) {
    const b = h.boxes[cur];
    x.lineWidth = 2; x.strokeStyle = css('--hover');
    x.strokeRect(b[0], b[1], b[2] - b[0], b[3] - b[1]);
  }
  const s = sel && byId(sel);
  if (s && s.boxes[cur]) {
    const b = s.boxes[cur], A = css('--focal');
    x.save(); x.shadowColor = A; x.shadowBlur = 14; x.strokeStyle = A; x.lineWidth = 3;
    x.strokeRect(b[0] - 3, b[1] - 3, b[2] - b[0] + 6, b[3] - b[1] + 6); x.restore();
  }
  drawZoom();
  $('frameLbl').innerHTML = `frame <b>${cur + 1}</b>/${doc.n_frames} &middot; ` +
    `${(cur / doc.fps).toFixed(2)}s &middot; clip at ${doc.fps} fps`;
}

function drawZoom() {
  const z = $('zoom'), zx = z.getContext('2d');
  zx.fillStyle = '#000'; zx.fillRect(0, 0, z.width, z.height);
  const s = sel && byId(sel);
  const b = s && s.boxes[cur];
  if (!b || !imgs[cur] || !imgs[cur].complete) return;
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  const R = Math.max(60, 2.0 * Math.max(b[2] - b[0], b[3] - b[1]));
  const sx = Math.max(0, cx - R), sy = Math.max(0, cy - R);
  const sw = Math.min(2 * R, doc.w - sx), sh = Math.min(2 * R, doc.h - sy);
  zx.drawImage(imgs[cur], sx, sy, sw, sh, 0, 0, z.width, z.height);
  zx.lineWidth = 2; zx.strokeStyle = SCOL[s.fstate[cur]] || css('--focal');
  zx.strokeRect((b[0] - sx) * z.width / sw, (b[1] - sy) * z.height / sh,
                (b[2] - b[0]) * z.width / sw, (b[3] - b[1]) * z.height / sh);
}

function drawRows() {
  document.querySelectorAll('.row').forEach(el => {
    const b = byId(el.dataset.ind); if (!b) return;
    el.classList.toggle('sel', b.individual_id === sel);
    el.classList.toggle('dismissed', dismissed(b));
    const glyph = b.status === 'merged' ? '⊝' : b.status === 'discarded' ? '⊘'
                : b.status === 'confirmed' ? '✓' : '○';
    const col = dismissed(b) ? '#5a6470'
              : b.status === 'confirmed' ? css('--ok')
              : (b.individual_id === sel ? css('--focal') : '#5a6470');
    const tip = b.status === 'merged' ? ''
              : b.status === 'discarded' ? ' title="click: restore this row (X)"'
              : b.status === 'confirmed' ? ' title="click: back to unannotated (U)"'
              : ' title="click: confirm this individual (C)"';
    el.querySelector('.lbl').innerHTML =
      `<span class="gl${b.status === 'merged' ? '' : ' hit'}" style="color:${col}"${tip}>` +
      `${glyph}</span><span style="color:${col}"> ${b.seed_track_id}</span>` +
      (b.needs_review ? ' <span style="color:var(--focal)">◤</span>' : '');
    el.querySelector('.pct').textContent =
      dismissed(b) ? '' : (100 * primaryFrac(b.fstate)).toFixed(0) + '%';
    drawBar(el.querySelector('.bar'), b);
    drawThumb(el.querySelector('.th'), b);
  });
}

function drawBar(cv, b) {
  cv.width = cv.clientWidth || 200;
  const x = cv.getContext('2d'), W = cv.width, H = cv.height, n = doc.n_frames;
  x.clearRect(0, 0, W, H);
  if (b.status === 'unseen') {
    // Hatch only where the individual actually has frames. Hatching the full width made
    // a two-frame tracker fragment look like an animal present for the whole clip.
    const real = new RegExp('[^' + NODATA + ']');
    const lo = b.fstate.search(real);
    const hi = b.fstate.length - 1 -
               [...b.fstate].reverse().join('').search(real);
    x.fillStyle = SCOL[NODATA]; x.fillRect(0, 0, W, H);
    if (lo >= 0) {
      const x0 = lo / n * W, x1 = (hi + 1) / n * W;
      x.save(); x.beginPath(); x.rect(x0, 0, Math.max(1, x1 - x0), H); x.clip();
      x.fillStyle = '#1a2027'; x.fillRect(x0, 0, Math.max(1, x1 - x0), H);
      x.strokeStyle = '#242e38'; x.lineWidth = 3;
      for (let i = -H; i < W; i += 8) {
        x.beginPath(); x.moveTo(i, H); x.lineTo(i + H, 0); x.stroke();
      }
      x.restore();
    }
  } else {
    for (let f = 0; f < n;) {
      let g = f + 1; while (g < n && b.fstate[g] === b.fstate[f]) g++;
      x.fillStyle = SCOL[b.fstate[f]] || '#232c37';
      x.fillRect(f / n * W, 0, (g - f) / n * W + 0.6, H);
      f = g;
    }
  }
  (b.segments || []).filter(s => s.by === 'human').forEach(s => {
    x.fillStyle = '#fff'; x.fillRect(s.from / n * W - 1, 0, 2, 4);   // re-anchor notch
  });
  x.strokeStyle = '#fff'; x.lineWidth = 1;
  x.beginPath(); x.moveTo(cur / n * W, 0); x.lineTo(cur / n * W, H); x.stroke();
}

function drawThumb(cv, b) {
  const x = cv.getContext('2d');
  x.fillStyle = '#2b333d'; x.fillRect(0, 0, cv.width, cv.height);
  let f = cur, box = b.boxes[f];
  while (box == null && f > 0) box = b.boxes[--f];       // hold the last crop, dimmed
  if (!box || !imgs[f] || !imgs[f].complete) return;
  x.globalAlpha = (f === cur) ? 1 : 0.45;
  const pad = 3;
  x.drawImage(imgs[f], Math.max(0, box[0] - pad), Math.max(0, box[1] - pad),
              box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad,
              0, 0, cv.width, cv.height);
  x.globalAlpha = 1;
}

function drawSelInfo() {
  const b = sel && byId(sel);
  if (!b) { $('selInfo').innerHTML = '&mdash;'; return; }
  const st = b.fstate[cur];
  const chain = (b.segments || []).filter(s => s.track_id !== null)
    .map(s => s.track_id).join(' → ');
  $('selInfo').innerHTML =
    `<b>track ${b.seed_track_id}</b>` +
    (requireZone() ? ` &middot; zone ${b.zone || '?'}` : '') + '<br>' +
    `state <b style="color:${SCOL[st]}">${SNAME[st] || st}</b><br>` +
    `${SNAME[CFG.states.active]} <b>${(100 * primaryFrac(b.fstate)).toFixed(0)}%</b> ` +
    `of ${b.n_present} frames<br>chain: ${chain || '—'}`;
}

function updateHeader() {
  const l = live(), done = l.filter(b => b.status === 'confirmed').length;
  $('clipProg').innerHTML = `individuals <b>${done}</b> / ${l.length}`;
  $('completeBtn').disabled = CFG.census_mode
    ? (done < l.length || l.length === 0) : false;
  syncClipEntry();
  const ig = ignored().length;
  const dis = doc.individuals.filter(b => b.status === 'discarded').length;
  const note = $('gridNote');
  if (note) note.textContent = [
    doc.complete ? 'this clip is marked complete' : '',
    ig ? `${ig} unclaimed row(s): drawn on the video, not counted here` : '',
    dis ? `${dis} discarded` : ''].filter(Boolean).join(' · ');
}

// ---- playback ---------------------------------------------------------------------
function stopPlay() {
  playing = false;
  if (timer) clearInterval(timer);
  timer = null;
  $('playBtn').innerHTML = '&#9654; Play';
}
function play() {
  if (playing || !doc) return;
  playing = true;
  $('playBtn').innerHTML = '&#10073;&#10073; Pause';
  timer = setInterval(() => {
    if (cur >= doc.n_frames - 1) { stopPlay(); return; }
    cur++; paintHeld(); drawAll();
  }, 1000 / fps);
}
$('playBtn').onclick = () => playing ? stopPlay() : play();
$('spd').onchange = e => { fps = +e.target.value; if (playing) { stopPlay(); play(); } };

// ---- editing ----------------------------------------------------------------------
function snapshot() {
  undoStack.push(JSON.stringify(doc.individuals.map(
    b => ({i: b.individual_id, f: b.fstate, s: b.status}))));
  if (undoStack.length > 200) undoStack.shift();
}

async function undo() {
  if (!undoStack.length) return toast('nothing to undo');
  clearTimeout(saveTimer); saveTimer = null; savePending = null;  // a pending debounce
  const changed = [];                                            // would re-apply the undo
  JSON.parse(undoStack.pop()).forEach(o => {
    const b = byId(o.i); if (!b) return;
    const fs = b.fstate !== o.f, st = b.status !== o.s;
    if (fs || st) changed.push({b, fs, st});
    b.fstate = o.f; b.status = o.s;
  });
  if (changed.some(c => c.st)) buildGrid();      // a restored row rejoins the grid
  drawAll();
  for (const c of changed) {
    if (c.fs) await postPaint(c.b);
    if (c.st) await postStatus(c.b);
  }
  toast('undone');
}

// Mirrors core.apply_geometry: '-' is geometry, and a painted state may not touch it,
// because judging a behavioural state is what a box is for. 'm' may, because a track that
// died does not mean the animal left, and "the animal is there, the box is not" is
// precisely what 'm' records.
function setChar(b, f, ch) {
  if (b.fstate[f] === NODATA && ch !== MISSED) return;
  b.fstate = b.fstate.slice(0, f) + ch + b.fstate.slice(f + 1);
}

const postPaint = b => POST('/api/paint',
  {clip: doc.clip, individual_id: b.individual_id, fstate: b.fstate});
const postConfirm = (b, confirmed) => POST('/api/confirm',
  {clip: doc.clip, individual_id: b.individual_id, confirmed});

// Undo replays a snapshot of statuses, and a status is one of four. /api/confirm refuses
// a dismissed row, so a row on its way back from `discarded` is restored first; restoring
// one that is not discarded is a no-op server side.
async function postStatus(b) {
  if (b.status === 'merged') return;          // merges are undone by re-anchoring
  const d = {clip: doc.clip, individual_id: b.individual_id};
  if (b.status === 'discarded') return POST('/api/discard', {...d, discarded: true});
  await POST('/api/discard', {...d, discarded: false});
  return postConfirm(b, b.status === 'confirmed');
}

function queueSave(b) {
  if (savePending && savePending.individual_id !== b.individual_id) flushSave();
  savePending = b;                            // one timer, many rows: never drop the other
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 1000);
}
function flushSave() {
  clearTimeout(saveTimer); saveTimer = null;
  const b = savePending; savePending = null;
  if (b) postPaint(b);
}
async function saveNow(b) {
  if (savePending && savePending.individual_id !== b.individual_id) flushSave();
  clearTimeout(saveTimer); saveTimer = null; savePending = null;
  const r = await (await postPaint(b)).json();
  if (r.individuals) { doc.individuals = r.individuals; drawAll(); }
}

async function setStatus(b, confirmed) {
  b.status = confirmed ? 'confirmed' : 'unseen';
  await postConfirm(b, confirmed);
  updateHeader();
}

function nextUnseen() {
  const l = live(), i = l.findIndex(b => b.individual_id === sel);
  const after = l.slice(i + 1).find(b => b.status === 'unseen');
  const wrapped = after || l.find(b => b.status === 'unseen');
  if (wrapped) { selectBird(wrapped.individual_id); scrollSelIntoView(); }
  else toast('every individual in this clip is confirmed');
}
function scrollSelIntoView() {
  const el = document.querySelector(`.row[data-ind="${sel}"]`);
  if (el) el.scrollIntoView({block: 'nearest'});
}

async function confirmRow() {
  const b = sel && byId(sel); if (!b) return;   // accept as painted, never rewrite fstate
  if (b.status === 'merged')
    return toast('that row is merged; re-anchor to undo the merge');
  snapshot();
  await setStatus(b, true);
  nextUnseen(); drawAll();
}

// Not every tracker row is an animal: a duplicate box on an already tracked individual,
// or a fragment detected for a frame or two, can neither be annotated nor allowed to hold
// the clip open forever. Discarding takes the row out of the grid and out of the
// completion check; the row stays in the file and can be restored.
async function discardRow(row, on) {
  const b = row || (sel && byId(sel)); if (!b) return;
  if (b.status === 'merged')
    return toast('that row is merged; re-anchor to undo the merge');
  const want = on === undefined ? b.status !== 'discarded' : !!on;
  if (want === (b.status === 'discarded')) return;
  const at = live().findIndex(r => r.individual_id === b.individual_id);
  snapshot();
  b.status = want ? 'discarded' : 'unseen';
  const r = await (await POST('/api/discard',
    {clip: doc.clip, individual_id: b.individual_id, discarded: want})).json();
  if (!r.ok) {
    b.status = want ? 'unseen' : 'discarded';   // put it back the way the server has it
    return toast(r.err || 'refused');
  }
  if (!want) doc.complete = false;
  if (want && b.individual_id === sel) {
    const l = live();
    selectBird(l.length ? l[Math.min(at, l.length - 1)].individual_id : null);
  }
  buildGrid(); drawAll(); loadStats();
  toast(want ? `track ${b.seed_track_id} discarded · Z to undo`
             : `track ${b.seed_track_id} restored`);
}

async function resetRow(row) {
  if (!row || row.status === 'merged') return;
  const id = row.individual_id, tid = row.seed_track_id;
  snapshot();
  // untouched has to mean untouched: every painted state and 'm' go back to the
  // baseline, and '-' survives because it is geometry.
  const rest = CFG.states.rest;
  row.fstate = [...row.fstate].map(c => c === NODATA ? c : rest).join('');
  await saveNow(row);
  const b = byId(id); if (!b) return;           // saveNow adopts the server's rows
  await setStatus(b, false);
  drawAll(); toast(`track ${tid} back to unannotated`);
}

addEventListener('keydown', e => {
  if (document.querySelector('.modal.on')) return;
  if (!doc) return;
  const t = e.target.tagName;
  if (t === 'SELECT' || t === 'INPUT' || t === 'TEXTAREA') return;
  const k = e.key.toLowerCase();
  if (k === ' ') { e.preventDefault(); playing ? stopPlay() : play(); return; }
  if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); return; }
  if (e.key === 'ArrowRight') { e.preventDefault(); step(1); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); moveSel(-1); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); moveSel(1); return; }
  if (e.key === 'Tab') { e.preventDefault(); cycleCandidate(); return; }
  if (k === 'z') { e.preventDefault(); undo(); return; }
  if (k === 'c') { e.preventDefault(); confirmRow(); return; }
  if (k === 'u') { e.preventDefault(); resetRow(sel && byId(sel)); return; }
  if (k === 'x') { e.preventDefault(); discardRow(sel && byId(sel)); return; }
  if (k === 'n') { e.preventDefault(); $('nextBtn').click(); return; }
  if (e.key === 'Enter') { e.preventDefault(); $('completeBtn').click(); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault(); $('skipBtn').click(); return;
  }
  if (k in PAINT) {
    e.preventDefault();
    const b = sel && byId(sel); if (!b) return;
    if (!e.repeat) { snapshot(); held = PAINT[k]; }
    setChar(b, cur, PAINT[k]);
    if (b.status === 'unseen') setStatus(b, true);
    queueSave(b); drawAll();
  }
});
addEventListener('keyup', e => { if (PAINT[e.key.toLowerCase()] === held) held = null; });
addEventListener('blur', () => { held = null; });

function step(d) {
  cur = Math.min(doc.n_frames - 1, Math.max(0, cur + d));
  paintHeld(); drawAll();
}
function paintHeld() {
  if (!held) return;
  const b = sel && byId(sel); if (!b) return;
  setChar(b, cur, held); queueSave(b);
}
function selectBird(id) {
  if (id === sel) return;
  sel = id;                    // every individual is judged from ITS first frame, not the
  const b = byId(id);          // clip's: a two-frame fragment has no box at frame 0, so
  const f = b ? (b.boxes || []).findIndex(Boolean) : -1;   // arriving there showed nothing
  cur = f < 0 ? 0 : f;
}
function moveSel(d) {
  const l = live();
  if (!l.length) return;
  const i = l.findIndex(b => b.individual_id === sel);
  const j = Math.min(l.length - 1, Math.max(0, i + d));
  selectBird(l[j].individual_id); scrollSelIntoView(); drawAll();
}

// ---- clip level actions -----------------------------------------------------------
$('completeBtn').onclick = async () => {
  const r = await (await POST('/api/complete', {clip: doc.clip})).json();
  if (r.ok) {
    doc.complete = true; toast('clip marked complete'); updateHeader(); loadStats();
  } else {
    toast(`still ${r.total - r.done} individual(s) unconfirmed`);
  }
};
$('skipBtn').onclick = async () => {
  if (!confirm('Skip this clip entirely? It will be moved to the finished group.')) return;
  await POST('/api/skip', {clip: doc.clip});
  clips[curClip].skipped = true; buildClipList(curClip); toast('skipped');
};
$('nextBtn').onclick = async () => {
  const n = nextOpenClip();
  if (n < 0) return toast('nothing left to annotate');
  await loadClip(n); toast(clips[n].id);
};
$('expBtn').onclick = async () => {
  toast('writing CSV…');
  const r = await (await POST('/api/export', {})).json();
  if (!r.ok) return toast(r.err || 'export failed');
  const rep = r.report;
  toast(`exported ${rep.clips} clip(s): ` +
        ['frames', 'bouts', 'units'].map(s => `${rep[s].rows} ${s}`).join(', '));
};

// ---- identity editing: the VIDEO is the identity surface ------------------------
// Hit-testing runs over the same overlay the video draws, orphans included: a box you can
// see but cannot click is worse than no box at all, and an orphan is exactly the box the
// human is there to re-anchor onto.
function boxesAt(px, py) {
  const inside = [], near = [];
  overlay(cur).forEach(({bird: b, box: q}) => {
    if (px >= q[0] && px <= q[2] && py >= q[1] && py <= q[3]) inside.push(b.individual_id);
    const cx = (q[0] + q[2]) / 2, cy = (q[1] + q[3]) / 2, w = q[2] - q[0];
    if ((cx - px) ** 2 + (cy - py) ** 2 < (1.5 * w) ** 2) near.push(b.individual_id);
  });
  return inside.length ? inside : near;
}
function cvCoords(e) {
  const r = $('cv').getBoundingClientRect();
  return [(e.clientX - r.left) * doc.w / r.width, (e.clientY - r.top) * doc.h / r.height];
}
$('cv').onmousemove = e => {
  if (!doc) return;
  const [px, py] = cvCoords(e);
  cands = boxesAt(px, py); candPos = 0;
  hoverBird = cands.length ? cands[0] : null;
  drawVideo();
};
$('cv').onmouseleave = () => { hoverBird = null; drawVideo(); };
function cycleCandidate() {
  if (!cands.length) return;
  candPos = (candPos + 1) % cands.length;
  hoverBird = cands[candPos];
  drawVideo();
}
$('cv').onclick = async () => {
  if (!hoverBird || !sel) return;
  if (hoverBird === sel) return toast('that is already this row');
  const target = byId(hoverBird);
  const tid = activeTrack(target.segments, cur);
  if (tid == null) return;
  stopPlay();
  snapshot();
  const r = await (await POST('/api/reanchor',
    {clip: doc.clip, individual_id: sel, frame: cur, track_id: tid})).json();
  if (!r.ok) return toast(r.err || 'refused');
  doc.individuals = r.individuals;
  buildGrid(); drawAll();
  const merged = doc.individuals.filter(
    b => b.status === 'merged' && b.merged_into === sel).length;
  const split = doc.individuals.filter(b => b.source === 'auto_split').length;
  toast(`re-anchored from frame ${cur + 1} onto track ${tid}` +
        (merged ? ` · ${merged} row merged in` : '') +
        (split ? ` · ${split} split row(s) present` : ''));
};

// ---- timeline -------------------------------------------------------------------
// paintRange and boundaries mirror core.paint_range / core.boundaries exactly. The Python
// copies are the tested ones; these exist so a drag can repaint locally and feel live.
function paintRange(fstate, start, end, ch, backward) {
  const n = fstate.length;
  start = Math.max(0, Math.min(n, start));
  end = Math.max(0, Math.min(n, end));
  if (start >= end) return fstate;
  const out = fstate.split('');
  const order = [];
  for (let f = start; f < end; f++) order.push(f);
  if (backward) order.reverse();
  for (const f of order) {
    if (out[f] === NODATA) break;
    out[f] = ch;
  }
  return out.join('');
}
function boundaries(fstate) {
  const out = [];
  for (let f = 1; f < fstate.length; f++) {
    if (fstate[f] !== fstate[f - 1] && fstate[f] !== NODATA && fstate[f - 1] !== NODATA)
      out.push(f);
  }
  return out;
}

const tlW = () => $('track').width;
const frameX = f => f / doc.n_frames * tlW();
const frameAt = px => Math.max(0, Math.min(doc.n_frames - 1,
                                           Math.round(px / tlW() * doc.n_frames)));
const tlX = e => e.clientX - $('track').getBoundingClientRect().left;

function drawTimeline() {
  const t = $('track'), x = t.getContext('2d'), W = t.width, H = t.height;
  x.clearRect(0, 0, W, H);
  const b = sel && byId(sel);
  if (!b) { drawRuler(); return; }
  const n = doc.n_frames;
  for (let f = 0; f < n;) {
    let g = f + 1; while (g < n && b.fstate[g] === b.fstate[f]) g++;
    x.fillStyle = SCOL[b.fstate[f]] || '#232c37';
    x.fillRect(frameX(f), 0, frameX(g) - frameX(f) + 0.5, H);
    f = g;
  }
  boundaries(b.fstate).forEach(f => {
    const hot = f === hotBound;
    x.fillStyle = hot ? '#fff' : 'rgba(255,255,255,.5)';
    x.fillRect(frameX(f) - (hot ? 2 : 0.5), 0, hot ? 4 : 1, H);
  });
  x.strokeStyle = '#fff'; x.lineWidth = 2;
  x.beginPath(); x.moveTo(frameX(cur), 0); x.lineTo(frameX(cur), H); x.stroke();
  drawRuler();
}

function drawRuler() {
  const r = $('ruler'), x = r.getContext('2d'), W = r.width, H = r.height;
  x.clearRect(0, 0, W, H);
  x.fillStyle = '#6d7986'; x.font = '9px -apple-system,Segoe UI,Roboto,Arial';
  // One tick a second and a label every five, whatever the clip's frame rate is.
  const perSec = Math.max(1, Math.round(doc.fps));
  for (let f = 0; f < doc.n_frames; f += perSec) {
    const px = frameX(f), major = (f / perSec) % 5 === 0;
    x.fillRect(px, H - (major ? 6 : 3), 1, major ? 6 : 3);
    if (major) x.fillText((f / doc.fps).toFixed(0) + 's', px + 3, 9);
  }
  const px = frameX(cur);                     // playhead grab handle
  x.fillStyle = '#fff';
  x.beginPath();
  x.moveTo(px - 5, 0); x.lineTo(px + 5, 0); x.lineTo(px, H); x.closePath(); x.fill();
}

function nearBoundary(fstate, px) {
  let best = null, bd = 6;
  for (const f of boundaries(fstate)) {
    const d = Math.abs(frameX(f) - px);
    if (d < bd) { bd = d; best = f; }
  }
  return best;
}
function barFrame(e) {
  const cv = drag && drag.bar ? drag.bar : e.currentTarget;
  const r = cv.getBoundingClientRect();
  return Math.max(0, Math.min(doc.n_frames - 1,
    Math.round((e.clientX - r.left) / r.width * doc.n_frames)));
}

$('tl').onmousedown = e => {
  if (!doc) return;
  const b = sel && byId(sel); if (!b) return;
  e.preventDefault(); stopPlay();
  const px = tlX(e), f = nearBoundary(b.fstate, px);
  if (f !== null) {
    snapshot();
    drag = {mode: 'bound', at: f, left: b.fstate[f - 1], right: b.fstate[f],
            base: b.fstate};
  } else {
    drag = {mode: 'scrub'};
    cur = frameAt(px); drawAll();
  }
};
$('tl').onmousemove = e => {
  if (drag || !doc) return;
  const b = sel && byId(sel); if (!b) return;
  hotBound = nearBoundary(b.fstate, tlX(e));
  $('tl').style.cursor = hotBound !== null ? 'col-resize' : 'pointer';
  drawTimeline();
};
$('tl').onmouseleave = () => { if (!drag) { hotBound = null; drawTimeline(); } };

addEventListener('mousemove', e => {
  if (!drag) return;
  const k2 = frameAt(tlX(e));
  if (drag.mode === 'scrub') { cur = drag.bar ? barFrame(e) : k2; drawAll(); return; }
  const b = byId(sel); if (!b) return;
  const k = drag.at;                     // always repaint from the pre-drag string, so
  let s = drag.base;                     // dragging back undoes what went before
  if (k2 > k) s = paintRange(drag.base, k, k2, drag.left);
  else if (k2 < k) s = paintRange(drag.base, k2, k, drag.right, true);
  b.fstate = s;
  cur = k2; drawAll();
});
addEventListener('mouseup', () => {
  if (!drag) return;
  const mode = drag.mode;
  drag = null;
  if (mode !== 'bound') return;
  const b = byId(sel); if (!b) return;
  if (b.status === 'unseen') setStatus(b, true);
  saveNow(b);
});

// ---- add video ------------------------------------------------------------------
let pendingVideo = null, pendingDet = null;

function openModal(id) { $(id).classList.add('on'); }
function closeModal(id) { $(id).classList.remove('on'); }

$('addBtn').onclick = () => { $('addLog').textContent = ''; openModal('addModal'); };
$('addCancel').onclick = () => closeModal('addModal');
$('setBtn').onclick = () => { $('setLog').textContent = ''; openModal('setModal'); };
$('setCancel').onclick = () => closeModal('setModal');
[...document.querySelectorAll('.modal')].forEach(m => {
  m.onclick = e => { if (e.target === m) m.classList.remove('on'); };
});

function pickVideo(file) {
  pendingVideo = file || null;
  $('vidName').innerHTML = file
    ? `<b>${file.name}</b> &middot; ${(file.size / 1048576).toFixed(1)} MB` : '';
  $('addGo').disabled = !file;
  if (file && !$('clipId').value) {
    $('clipId').placeholder = file.name.replace(/\.[^.]+$/, '')
      .replace(/[^\w\-.]/g, '_');
  }
}
$('vidFile').onchange = e => pickVideo(e.target.files[0]);
$('detFile').onchange = e => {
  pendingDet = e.target.files[0] || null;
  $('detName').textContent = pendingDet ? pendingDet.name : '';
};
const drop = $('drop');
['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => {
  e.preventDefault(); drop.classList.add('hot');
}));
['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => {
  e.preventDefault(); drop.classList.remove('hot');
}));
drop.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if (f) pickVideo(f);
});

async function upload(file, kind) {
  const url = `/api/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`;
  const r = await (await fetch(url, {method: 'POST', body: file})).json();
  if (!r.ok) throw new Error(r.err || 'upload failed');
  return r;
}

$('addGo').onclick = async () => {
  if (!pendingVideo) return;
  const log = m => { $('addLog').innerHTML += m + '<br>'; };
  $('addGo').disabled = true;
  try {
    log(`uploading ${pendingVideo.name}…`);
    await upload(pendingVideo, 'video');
    let detName = null;
    if (pendingDet) {
      log(`uploading ${pendingDet.name}…`);
      detName = (await upload(pendingDet, 'detections')).path.split('/').pop();
    }
    const tags = {};
    ($('clipTags').value || '').split(/\s+/).filter(Boolean).forEach(kv => {
      const [k, ...rest] = kv.split('=');
      if (k && rest.length) tags[k] = rest.join('=');
    });
    log('starting ingest…');
    const r = await (await POST('/api/ingest', {
      video: pendingVideo.name, clip: $('clipId').value || null,
      detections: detName, tags, overwrite: $('ovw').checked})).json();
    if (!r.ok) throw new Error(r.err);
    await watchJob(r.job, log);
  } catch (e) {
    log('<b style="color:#ff9a9a">' + e.message + '</b>');
  } finally {
    $('addGo').disabled = false;
  }
};

async function watchJob(jid, log) {
  let seen = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 700));
    const jobs = await (await fetch('/api/jobs')).json();
    const j = jobs[jid];
    if (!j) return;
    for (; seen < j.log.length; seen++) log(j.log[seen]);
    if (j.state === 'done') {
      log('<b style="color:#8ef0c0">ingest finished</b>');
      await loadClips(); loadStats();
      return;
    }
    if (j.state === 'failed') {
      log('<b style="color:#ff9a9a">' + (j.error || 'ingest failed') + '</b>');
      return;
    }
  }
}

// ---- settings -------------------------------------------------------------------
const SET_FIELDS = ['extract_fps', 'playback_fps', 'frame_max_width', 'jpeg_quality',
                    'display_max_scale', 'window_frames', 'detector_model',
                    'detector_conf', 'detector_imgsz', 'tracker_kind', 'annotator'];

function fillSettings(s) {
  s = s || {};
  SET_FIELDS.forEach(k => {
    const el = $('s_' + k);
    if (el) el.value = (s[k] === null || s[k] === undefined) ? '' : s[k];
  });
  $('s_census_mode').checked = !!s.census_mode;
}

$('modelFile').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  $('modelName').textContent = `uploading ${f.name} (${(f.size / 1048576).toFixed(0)} MB)…`;
  try {
    const r = await upload(f, 'model');
    $('s_detector_model').value = r.path;
    $('modelName').textContent = 'uploaded, and set as this project\'s detector';
  } catch (err) {
    $('modelName').textContent = err.message;
  }
};

$('setGo').onclick = async () => {
  const body = {census_mode: $('s_census_mode').checked};
  const nums = {extract_fps: 1, playback_fps: 1, frame_max_width: 1, jpeg_quality: 1,
                display_max_scale: 1, window_frames: 1, detector_conf: 1,
                detector_imgsz: 1};
  const ints = {frame_max_width: 1, jpeg_quality: 1, window_frames: 1,
                detector_imgsz: 1};
  SET_FIELDS.forEach(k => {
    const el = $('s_' + k); if (!el) return;
    const raw = el.value.trim();
    if (k in nums) {
      if (raw === '') { body[k] = null; return; }
      body[k] = (k in ints) ? parseInt(raw, 10) : parseFloat(raw);
    } else {
      body[k] = raw;
    }
  });
  // playback_fps and display_max_scale have no meaningful null
  if (body.playback_fps === null) delete body.playback_fps;
  if (body.display_max_scale === null) delete body.display_max_scale;
  if (body.jpeg_quality === null) delete body.jpeg_quality;
  if (body.detector_conf === null) delete body.detector_conf;
  if (body.detector_imgsz === null) delete body.detector_imgsz;
  const r = await (await POST('/api/settings', body)).json();
  if (!r.ok) return void ($('setLog').textContent = r.err);
  $('setLog').innerHTML = '<b style="color:#8ef0c0">saved</b>';
  CFG.settings = r.settings;
  CFG.census_mode = !!r.settings.census_mode;
  if (r.settings.playback_fps) { fps = r.settings.playback_fps; buildSpeeds(); }
  if (r.settings.display_max_scale) {
    CFG.display_max_scale = r.settings.display_max_scale;
    if (doc) { sizeStage(); drawAll(); }
  }
  if (doc) updateHeader();
};

init();
