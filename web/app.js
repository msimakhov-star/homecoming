/* Homecoming front end: no framework, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const state = { files: { letter: [], boxes: [], organiser: [] }, photos: { letter: [], boxes: [], organiser: [] }, video: null, sample: null, result: null, rows: null };

const STEPS = [
  ['extract:letter', 'Reading the discharge letter', 'Nemotron 3.5 Super VL'],
  ['extract:boxes', 'Reading every box on the table', 'Nemotron 3.5 Super VL'],
  ['extract:organiser', 'Reading the pill organiser', 'Nemotron 3.5 Super VL'],
  ['normalise', 'Matching brand names to ingredients', 'RxNorm + Tavily'],
  ['compare', 'Comparing hospital, house and organiser', 'plain code, no model'],
  ['interpret', 'Auditing the letter and writing questions', 'Nemotron 3 Ultra'],
  ['guard', 'Checking every sentence gives no advice', 'Nemotron 3.5 Lightning'],
];

function toast(msg, ms = 3500) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), ms);
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------- capture ---------- */
document.querySelectorAll('.tile input').forEach(inp => inp.addEventListener('change', async () => {
  const g = inp.dataset.group;
  const picked = [...inp.files];
  const vid = picked.find(f => f.type.startsWith('video/'));
  let files = picked.filter(f => f.type.startsWith('image/'));
  if (g === 'boxes') state.video = vid || null;
  if (vid) {
    toast('Taking the sharpest frames from your video…');
    try { files = files.concat(await videoFrames(vid, 6)); } catch (e) { toast('Could not read that video; please add photos instead.'); }
  }
  state.files[g] = files;
  state.photos[g] = files.map(f => URL.createObjectURL(f));
  state.sample = null; syncSamples();
  renderThumbs(g);
}));

/* Pull n evenly spaced frames from a phone video, keeping the sharpest of three nearby candidates each time
   (sharpness = mean absolute Laplacian on a small greyscale copy). Frames are sent like photos, so the app
   works even where the model's native video input is unavailable. */
async function videoFrames(file, n) {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto'; v.src = URL.createObjectURL(file);
  await new Promise((ok, bad) => { v.onloadedmetadata = ok; v.onerror = bad; });
  const seek = t => new Promise(ok => { v.onseeked = ok; v.currentTime = Math.min(Math.max(t, 0), v.duration - 0.05); });
  const big = document.createElement('canvas'), small = document.createElement('canvas');
  const scale = Math.min(1, 1600 / Math.max(v.videoWidth, v.videoHeight));
  big.width = Math.round(v.videoWidth * scale); big.height = Math.round(v.videoHeight * scale);
  small.width = 160; small.height = Math.round(160 * v.videoHeight / v.videoWidth);
  const sharpness = () => {
    const c = small.getContext('2d', { willReadFrequently: true }); c.drawImage(v, 0, 0, small.width, small.height);
    const d = c.getImageData(0, 0, small.width, small.height).data, w = small.width; let sum = 0;
    const g = i => d[i * 4] * 0.3 + d[i * 4 + 1] * 0.59 + d[i * 4 + 2] * 0.11;
    for (let y = 1; y < small.height - 1; y++) for (let x = 1; x < w - 1; x++) {
      const i = y * w + x; sum += Math.abs(4 * g(i) - g(i - 1) - g(i + 1) - g(i - w) - g(i + w));
    }
    return sum;
  };
  const out = [], step = v.duration / n;
  for (let k = 0; k < n; k++) {
    let best = null;
    for (const off of [-0.25, 0, 0.25]) {
      await seek(step * (k + 0.5) + off * step);
      const s = sharpness();
      if (!best || s > best.s) { best = { s, t: v.currentTime }; }
    }
    await seek(best.t);
    big.getContext('2d').drawImage(v, 0, 0, big.width, big.height);
    const blob = await new Promise(ok => big.toBlob(ok, 'image/jpeg', 0.88));
    out.push(new File([blob], `frame${k + 1}.jpg`, { type: 'image/jpeg' }));
  }
  URL.revokeObjectURL(v.src);
  return out;
}
function renderThumbs(g) {
  $(`[data-thumbs="${g}"]`).innerHTML = state.photos[g].map(u => `<img src="${u}" alt="">`).join('');
}

async function loadSamples() {
  const list = await fetch('/api/samples').then(r => r.json());
  $('#sample-list').innerHTML = list.map(s => `<button type="button" data-sample="${s.index}" aria-pressed="false">${esc(s.name.replace(/^Kitchen \d+: /, ''))}</button>`).join('');
  $('#sample-list').addEventListener('click', e => {
    const b = e.target.closest('button[data-sample]'); if (!b) return;
    const s = list[+b.dataset.sample];
    state.sample = s.index;
    for (const g of ['letter', 'boxes', 'organiser']) { state.files[g] = []; state.photos[g] = [s.images[g]]; renderThumbs(g); }
    syncSamples();
    $('#run').click();
  });
}
function syncSamples() {
  document.querySelectorAll('[data-sample]').forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.sample === state.sample)));
}

/* ---------- run ---------- */
$('#run').addEventListener('click', async () => {
  if (state.sample === null && (!state.files.letter.length || !state.files.boxes.length)) { toast('Add the letter and a photo of the boxes, or pick a sample.'); return; }
  const fd = () => {
    const f = new FormData();
    if (state.sample !== null) f.append('sample', state.sample);
    else {
      for (const g of ['letter', 'boxes', 'organiser']) state.files[g].forEach(x => f.append(g, x, x.name));
      if (state.video && state.video.size < 20 * 1024 * 1024) f.append('walkthrough', state.video, state.video.name);
    }
    return f;
  };
  startRail(); $('#run').disabled = true; $('#results').hidden = true;
  const ctrl = new AbortController(); const tmo = setTimeout(() => ctrl.abort(), 150000);
  try {
    let res;
    try { res = await runStream(fd(), ctrl.signal); }
    catch (e) {  // streaming blocked by a proxy or older browser: plain request
      if (e.name === 'AbortError' || e.http) throw e;
      const r = await fetch('/api/run', { method: 'POST', body: fd(), signal: ctrl.signal });
      if (!r.ok) throw Object.assign(new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`), { http: true });
      res = await r.json();
    }
    if (res.sample_images) for (const g in res.sample_images) state.photos[g] = [res.sample_images[g]];
    await finishRail(); render(res);
  } catch (e) { await finishRail(false); toast(e.name === 'AbortError' ? 'That took too long. Please try again, or try a sample kitchen.' : 'Could not check the kitchen: ' + e.message, 6000); }
  clearTimeout(tmo); $('#run').disabled = false;
});

async function runStream(body, signal) {
  const r = await fetch('/api/run-stream', { method: 'POST', body, signal });
  if (!r.ok) throw Object.assign(new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`), { http: true });
  if (!r.body || !r.body.getReader) throw new Error('no streaming');
  const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error('stream ended early');
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 2);
      if (!line.startsWith('data: ')) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.result) return ev.result;
      if (ev.error) throw Object.assign(new Error(ev.error), { http: true });
      railEvent(ev);
    }
  }
}

/* The rail shows real server progress. Events from a recorded run arrive at once, so they are paced
   120 ms apart purely so the eye can follow them; each step says when it was recorded rather than live. */
let railQueue = Promise.resolve();
function startRail() {
  $('#progress').hidden = false;
  $('#rail').innerHTML = STEPS.map(([k, t, m]) => `<li data-step="${k}"><span class="dot"></span><span>${t}<small class="info"></small></span><span class="model">${m}</span></li>`).join('');
  railQueue = Promise.resolve();
  $('#progress').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function railEvent(ev) {
  railQueue = railQueue.then(() => new Promise(done => {
    const li = $(`#rail li[data-step="${ev.step}"]`); if (!li) return done();
    li.classList.toggle('active', ev.status === 'running');
    if (ev.status === 'done' || ev.status === 'skipped') li.classList.add('done');
    const bits = [];
    if (ev.status === 'skipped') bits.push('no photo');
    if (ev.items !== undefined) bits.push(`${ev.items} item${ev.items === 1 ? '' : 's'}`);
    if (ev.confirmed !== undefined) bits.push(`${ev.confirmed} of ${ev.total} confirmed`);
    if (ev.discrepancies !== undefined) bits.push(`${ev.discrepancies} disagreement${ev.discrepancies === 1 ? '' : 's'}`);
    if (ev.cards !== undefined && ev.status === 'done') bits.push(`${ev.cards} question${ev.cards === 1 ? '' : 's'}`);
    if (ev.rewritten !== undefined) bits.push(`${ev.rewritten} rephrased`);
    if (ev.ms !== undefined && !ev.recorded) bits.push(`${(ev.ms / 1000).toFixed(1)} s`);
    if (ev.recorded) bits.push('recorded');
    if (bits.length) $('.info', li).textContent = ' · ' + bits.join(' · ');
    setTimeout(done, ev.status === 'running' ? 0 : 120);
  }));
}
async function finishRail(ok = true) {
  await railQueue;
  [...$('#rail').children].forEach(li => li.classList.remove('active'));
  if (ok) await new Promise(r => setTimeout(r, 500)), ($('#progress').hidden = true);
}

/* ---------- render ---------- */
function render(res) {
  state.result = res; state.rows = res.rows;
  const R = $('#results'); R.hidden = false;
  const b = $('#banner'); b.hidden = !res.recorded; if (res.recorded) b.textContent = 'Recorded mode: ' + (res.reason || 'showing a saved result') + '. The models were not called for this run.';
  $('#no-cards').hidden = res.cards.length > 0;
  $('#cards').innerHTML = res.cards.map(card).join('');
  $('#audit-wrap').hidden = !res.letter_audit.length;
  $('#audit').innerHTML = res.letter_audit.map(a => `<li><strong>${esc(a.issue)}</strong> (${a.medicines.map(esc).join(', ')}) <br><em>${esc(a.question)}</em></li>`).join('');
  renderColumns(res);
  $('#recheck').hidden = false;
  $('#cost-total').textContent = '$' + Number(res.cost.total_usd || 0).toFixed(4);
  $('#cost-table tbody').innerHTML = (res.cost.calls || []).map(c => `<tr><td>${esc(c.step)}</td><td>${esc(c.model.replace('nvidia/', ''))}</td><td>${c.in}</td><td>${c.out}</td><td>${c.ms}</td><td>${c.usd.toFixed(5)}</td></tr>`).join('') || '<tr><td colspan="6">No model calls in recorded mode.</td></tr>';
  renderSheet(res);
  $('#share-link').hidden = true; $('#ask-answer').hidden = true;
  R.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function card(c) {
  const sev = { 'check-today': 'Check today', 'ask-when-convenient': 'Ask when convenient', 'info': 'For information' }[c.severity] || c.severity;
  const refs = [...c.letter_ids, ...c.found_ids].map(id => `<button class="ref" type="button" data-ref="${esc(id)}">${esc(id)}</button>`).join('');
  const srcs = Object.entries(c.sources || {}).filter(([, s]) => s && s.url).map(([id, s]) => `<a class="ref src" target="_blank" rel="noopener" href="${esc(s.url)}">${esc(id)}: ${esc(s.source)} ↗</a>`).join('');
  return `<article class="card ${esc(c.severity)}">
    <div class="sev">${sev}</div>
    <h3>${esc(c.headline)}</h3>
    <div class="detail">${esc(c.detail)}</div>
    <div class="q"><b>Ask the pharmacist:</b> ${esc(c.question)}</div>
    <div class="refs">${refs}${srcs}${c.guard_rewrote ? '<span class="guarded">rephrased by the guard</span>' : ''}</div>
    ${photoFor(c)}
  </article>`;
}

function photoFor(c) {
  // Outline the first found item that has a grid cell on its photo.
  for (const id of c.found_ids) {
    const g = id[0] === 'B' ? 'boxes' : id[0] === 'O' ? 'organiser' : null; if (!g) continue;
    const row = (state.rows[g] || []).find(r => r.id === id); if (!row || !row.cell) continue;
    const src = state.photos[g][row.photo || 0]; if (!src) continue;
    const cells = Array.from({ length: 9 }, (_, i) => `<span class="${i + 1 === row.cell ? 'hit' : ''}"></span>`).join('');
    return `<div class="photo" title="${esc(id)} outlined"><img src="${src}" alt="Photo with ${esc(id)} outlined"><div class="grid">${cells}</div></div>`;
  }
  return '';
}

function renderColumns(res) {
  const act = ['new', 'stopped', 'changed', 'continued', 'withheld', 'none'];
  const row = (r, g) => `<li data-id="${esc(r.id)}" data-group="${g}">
      <span class="id">${esc(r.id)}</span>
      <input class="name" value="${esc(r.name_as_printed)}" aria-label="name">
      <input class="strength" value="${esc(r.strength || '')}" aria-label="strength" placeholder="strength">
      ${g === 'letter' ? `<span class="action">letter says <select class="act">${act.map(a => `<option ${a === r.action ? 'selected' : ''}>${a}</option>`).join('')}</select></span>` : ''}
      ${g === 'organiser' ? `<span class="action">slot: <input class="slot" value="${esc(r.slot || '')}" aria-label="slot" style="width:6rem"></span>` : ''}
      ${res.sources[r.id] && res.sources[r.id].confirmed === false ? `<span class="unconfirmed">ingredient not confirmed: read as “${esc(res.sources[r.id].ingredient)}”</span>` : ''}
      ${r.confidence && r.confidence !== 'high' ? `<span class="unsure">Not sure I read this right.
        <label class="rescan">Photograph it closer<input type="file" accept="image/*" capture="environment" data-rescan="${esc(r.id)}" data-group="${g}" hidden></label></span>` : ''}
    </li>`;
  $('#col-hospital').innerHTML = res.rows.letter.map(r => row(r, 'letter')).join('') || '<li>nothing read</li>';
  $('#col-house').innerHTML = res.rows.boxes.map(r => row(r, 'boxes')).join('') || '<li>nothing read</li>';
  $('#col-organiser').innerHTML = res.rows.organiser.map(r => row(r, 'organiser')).join('') || '<li>empty</li>';
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-ref]'); if (!b) return;
  const li = document.querySelector(`.rows li[data-id="${b.dataset.ref}"]`); if (!li) return;
  li.scrollIntoView({ behavior: 'smooth', block: 'center' }); li.classList.add('flash'); setTimeout(() => li.classList.remove('flash'), 1600);
});

document.addEventListener('change', async e => {
  const inp = e.target.closest('input[data-rescan]'); if (!inp || !inp.files[0]) return;
  const li = inp.closest('li'), g = inp.dataset.group, id = inp.dataset.rescan;
  const fd = new FormData(); fd.append('group', g); fd.append('image', inp.files[0]);
  li.classList.add('flash'); toast('Reading the close-up…');
  try {
    const r = await fetch('/api/rescan', { method: 'POST', body: fd });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    const row = state.rows[g].find(x => x.id === id);
    Object.assign(row, body.item, { confidence: body.item.confidence || 'high' });
    $('.name', li).value = row.name_as_printed; $('.strength', li).value = row.strength || '';
    $('#recheck').click();
  } catch (err) { toast(err.message, 5000); }
  li.classList.remove('flash');
});

$('#recheck').addEventListener('click', async () => {
  const rows = { letter: [], boxes: [], organiser: [] };
  document.querySelectorAll('.rows li[data-id]').forEach(li => {
    const g = li.dataset.group, orig = state.rows[g].find(r => r.id === li.dataset.id) || {};
    rows[g].push({ ...orig, name_as_printed: $('.name', li).value.trim(), strength: $('.strength', li).value.trim(),
      action: g === 'letter' ? $('.act', li).value : 'none', slot: g === 'organiser' ? $('.slot', li).value.trim() : '' });
  });
  $('#recheck').disabled = true;
  try {
    const r = await fetch('/api/reconcile', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const res = await r.json(); res.reason = state.result.reason; res.recorded = res.recorded || state.result.recorded;
    render(res); toast('Re-checked with your corrections.');
  } catch (err) { toast('Re-check failed: ' + err.message); }
  $('#recheck').disabled = false;
});

$('#ask-form').addEventListener('submit', async e => {
  e.preventDefault();
  const q = $('#ask-input').value.trim(); if (!q) return;
  const out = $('#ask-answer'); out.hidden = false; out.textContent = 'Thinking…';
  try {
    const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q, context: state.result }) });
    const res = await r.json(); out.textContent = res.answer;
  } catch (err) { out.textContent = 'Could not ask right now.'; }
});

/* ---------- sheet, print, share ---------- */
function sheetData(res) {
  return { date: new Date().toISOString().slice(0, 10), cards: res.cards.map(c => ({ headline: c.headline, detail: c.detail, question: c.question, severity: c.severity })),
    audit: res.letter_audit, hospital: res.columns.hospital, house: res.columns.house, organiser: res.columns.organiser };
}
const label = (name, strength) => !strength || name.toLowerCase().replace(/\s+/g, '').includes(strength.toLowerCase().replace(/\s+/g, '')) ? name : `${name} ${strength}`;
function renderSheet(res, data = sheetData(res), show = false) {
  const S = $('#sheet'); S.hidden = !show;  // on screen the sheet is print-only; the share page shows it
  S.innerHTML = `<h1>Questions for the pharmacist</h1>
    <p class="meta">Prepared ${esc(data.date)} with Homecoming from a discharge letter and the medicines found at home. Please check each point with us.</p>
    ${data.cards.length ? `<ol>${data.cards.map(c => `<li><strong>${esc(c.question)}</strong><br><span class="meta">${esc(c.headline)}. ${esc(c.detail)}</span></li>`).join('')}</ol>` : '<p>No disagreements were found between the letter and the medicines at home.</p>'}
    ${data.audit.length ? `<h3>About the letter itself</h3><ul>${data.audit.map(a => `<li>${esc(a.question)} <span class="meta">(${esc(a.issue)})</span></li>`).join('')}</ul>` : ''}
    <h3>Letter lists</h3><p class="meta">${data.hospital.map(h => `${esc(label(h.name, h.strength))} (${esc(h.action)})`).join(' · ') || 'nothing read'}</p>
    <h3>Found at home</h3><p class="meta">${data.house.map(h => esc(label(h.name, h.strength))).join(' · ') || 'nothing read'}${data.organiser.length ? ' · organiser: ' + data.organiser.map(o => `${esc(o.slot || '')} ${esc(label(o.name, o.strength))}`).join(', ') : ''}</p>
    <p class="fine">Homecoming only compares documents and photos. It gives no medical advice and does not say what anyone should take. Read by NVIDIA Nemotron on Nebius Token Factory; matched by plain code; every sentence checked for advice.</p>`;
}
$('#print').addEventListener('click', () => window.print());
$('#share').addEventListener('click', async () => {
  try {
    const r = await fetch('/api/share', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sheet: sheetData(state.result) }) });
    const { url } = await r.json(); const full = location.origin + url;
    const el = $('#share-link'); el.hidden = false; el.innerHTML = `Link (7 days, questions only, no photos): <a href="${full}">${full}</a>`;
    if (navigator.share) navigator.share({ title: 'Questions for the pharmacist', url: full }).catch(() => {});
    else if (navigator.clipboard) { await navigator.clipboard.writeText(full); toast('Link copied.'); }
  } catch (e) { toast('Could not create a link.'); }
});

/* ---------- boot ---------- */
(async function boot() {
  const m = location.pathname.match(/^\/s\/([\w-]+)$/);
  if (m) {  // shared sheet: hide everything but the sheet
    document.querySelectorAll('.hero, .capture').forEach(el => (el.hidden = true));
    try { const data = await fetch('/api/share/' + m[1]).then(r => { if (!r.ok) throw 0; return r.json(); }); renderSheet(null, data, true); }
    catch { $('#sheet').hidden = false; $('#sheet').innerHTML = '<h1>This link has expired</h1><p>Shared sheets live for 7 days.</p>'; }
    return;
  }
  loadSamples();
  try {
    const h = await fetch('/api/health').then(r => r.json());
    const chip = $('#mode-chip'); chip.textContent = h.live ? 'Live on Nebius Token Factory' : 'Recorded mode'; chip.classList.toggle('live', !!h.live);
  } catch { $('#mode-chip').textContent = 'offline'; }
})();
