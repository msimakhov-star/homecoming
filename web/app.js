/* Homecoming front end: no framework, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const state = { files: { letter: [], boxes: [], organiser: [] }, photos: { letter: [], boxes: [], organiser: [] }, sample: null, result: null, rows: null };

const STEPS = [
  ['Reading the discharge letter', 'Nemotron 3.5 Super VL'],
  ['Reading every box on the table', 'Nemotron 3.5 Super VL'],
  ['Reading the pill organiser', 'Nemotron 3.5 Super VL'],
  ['Matching brand names to ingredients', 'RxNorm + Tavily'],
  ['Comparing hospital, house and organiser', 'plain code, no model'],
  ['Auditing the letter and writing questions', 'Nemotron 3 Ultra'],
  ['Checking every sentence gives no advice', 'Nemotron 3.5 Lightning'],
];

function toast(msg, ms = 3500) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), ms);
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------- capture ---------- */
document.querySelectorAll('.tile input').forEach(inp => inp.addEventListener('change', () => {
  const g = inp.dataset.group;
  state.files[g] = [...inp.files];
  state.photos[g] = state.files[g].map(f => URL.createObjectURL(f));
  state.sample = null; syncSamples();
  renderThumbs(g);
}));
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
  });
}
function syncSamples() {
  document.querySelectorAll('[data-sample]').forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.sample === state.sample)));
}

/* ---------- run ---------- */
$('#run').addEventListener('click', async () => {
  if (state.sample === null && (!state.files.letter.length || !state.files.boxes.length)) { toast('Add the letter and a photo of the boxes, or pick a sample.'); return; }
  const fd = new FormData();
  if (state.sample !== null) fd.append('sample', state.sample);
  else for (const g of ['letter', 'boxes', 'organiser']) state.files[g].forEach(f => fd.append(g, f, f.name));
  startRail(); $('#run').disabled = true; $('#results').hidden = true;
  try {
    const r = await fetch('/api/run', { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    const res = await r.json();
    if (res.sample_images) for (const g in res.sample_images) state.photos[g] = [res.sample_images[g]];
    finishRail(); render(res);
  } catch (e) { finishRail(false); toast('Could not check the kitchen: ' + e.message, 6000); }
  $('#run').disabled = false;
});

let railTimer;
function startRail() {
  $('#progress').hidden = false;
  $('#rail').innerHTML = STEPS.map(([t, m]) => `<li><span class="dot"></span>${t}<span class="model">${m}</span></li>`).join('');
  let i = 0; const items = [...$('#rail').children];
  const tick = () => { items.forEach((li, j) => li.classList.toggle('active', j === i)); items.slice(0, i).forEach(li => li.classList.add('done')); if (i < items.length - 1) i++; };
  tick(); railTimer = setInterval(tick, 1400);
  $('#progress').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function finishRail(ok = true) {
  clearInterval(railTimer);
  [...$('#rail').children].forEach(li => { li.classList.remove('active'); if (ok) li.classList.add('done'); });
  if (ok) setTimeout(() => ($('#progress').hidden = true), 600);
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
