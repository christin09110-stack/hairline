// Hairline's interface. Reuses servicekit's HTTP wrapper and nothing else: the shell's
// generic result renderer would print a crack schedule as a JSON dump, and the whole
// point of this product is that the schedule is a document an engineer can act on.

import { api, ApiError } from '/shell/js/api.js';

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const mm = (v, digits = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits));

const ui = {
  form: $('upload-form'), dropzone: $('dropzone'), fileInput: $('file-input'),
  fileLabel: $('dropzone-file'), hint: $('dropzone-hint'), params: $('params'),
  submit: $('submit'), samples: $('sample-picker'),
  progressPanel: $('progress-panel'), bar: $('progress-bar'),
  message: $('progress-message'), percent: $('progress-percent'),
  status: $('job-status'), log: $('log'),
  result: $('result'), evidenceBlock: $('evidence-block'),
  evidence: $('evidence'), evidenceCount: $('evidence-count'),
};

let selectedFile = null;
let closeStream = null;
let paramSpecs = [];

// ------------------------------------------------------- calibration gate ----
// docs/design/hairline-spec.md section 6: if the build's calibration test against
// printed lines of known width drifts, the product refuses to publish any width at
// all and says so. This is the "says so" half; service.py holds the refusal.

async function loadCalibration() {
  let report;
  try {
    report = await (await fetch('/api/calibration')).json();
  } catch {
    $('stat-calibration').textContent = 'unknown';
    return;
  }
  const chip = $('stat-calibration');
  chip.textContent = report.ok ? `ok, ±${report.worst_error_pct ?? '?'}%` : 'FAILED';
  chip.style.color = report.ok ? 'var(--ok)' : 'var(--signal)';
  if (report.ok) return;

  const banner = el('section', 'gate');
  banner.id = 'calibration-gate';
  banner.append(el('h2', null, 'This build is not publishing widths'));
  banner.append(el('p', null,
    'Its calibration check against printed lines of known width did not pass, so every '
    + 'width it produced would be suspect. ' + (report.failure || '')));
  banner.append(el('p', null,
    `Checked ${report.checked_at}, against a ${report.tolerance_pct}% tolerance.`));
  $('calibration-banner').replaceChildren(banner);
  ui.submit.disabled = true;
}

// ------------------------------------------------------------ capture check ----
// The spec puts this above the report screen, and it is right: every refusal in a
// report is a walk somebody has already done. This tells the operator the angle
// while they are still standing in front of the wall.

const captureInput = document.createElement('input');
captureInput.type = 'file';
captureInput.accept = 'image/*';
captureInput.capture = 'environment';

$('capture-check').addEventListener('click', () => captureInput.click());
captureInput.addEventListener('change', async () => {
  const file = captureInput.files?.[0];
  if (!file) return;
  const box = $('capture-advice');
  box.hidden = false;
  box.className = 'advice';
  box.replaceChildren(el('b', null, 'Checking…'));
  const body = new FormData();
  body.append('file', file);
  try {
    const response = await fetch('/api/capture?target_width_mm=0.30', { method: 'POST', body });
    const advice = await response.json();
    if (!response.ok) throw new Error(advice?.error?.message ?? 'check failed');
    box.className = `advice advice--${advice.state}`;
    box.replaceChildren(el('b', null, advice.headline), el('span', null, advice.detail));
    if (advice.px_per_mm) {
      const dl = el('dl');
      const rows = [
        ['Off square', `${advice.apparent_tilt_deg}°`],
        ['Scale', `${advice.px_per_mm} px/mm`],
        ['Finest measurable', `${advice.finest_measurable_mm} mm`],
      ];
      for (const [k, v] of rows) { dl.append(el('dt', null, k), el('dd', null, v)); }
      box.append(dl);
    }
  } catch (error) {
    box.className = 'advice advice--not-ready';
    box.replaceChildren(el('b', null, 'Could not check that frame'), el('span', null, String(error.message ?? error)));
  }
});

// ---------------------------------------------------------------- input ----

function setFile(file, label) {
  selectedFile = file;
  ui.fileLabel.textContent = file
    ? (label || `${file.name} · ${(file.size / (1024 * 1024)).toFixed(1)} MB`)
    : 'Drop a clip, or click to choose';
  ui.submit.disabled = !file;
}

ui.dropzone.addEventListener('click', () => ui.fileInput.click());
ui.fileInput.addEventListener('change', () => setFile(ui.fileInput.files[0] ?? null));
for (const name of ['dragenter', 'dragover']) {
  ui.dropzone.addEventListener(name, (e) => { e.preventDefault(); ui.dropzone.dataset.dragging = 'true'; });
}
for (const name of ['dragleave', 'drop']) {
  ui.dropzone.addEventListener(name, (e) => { e.preventDefault(); ui.dropzone.dataset.dragging = 'false'; });
}
ui.dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) setFile(file);
});

function renderParams(specs) {
  ui.params.replaceChildren();
  for (const spec of specs) {
    const field = el('div', 'field');
    const label = el('label', null, spec.label ?? spec.name);
    label.htmlFor = `p-${spec.name}`;
    if (spec.unit) label.append(' ', el('span', 'field__unit', `(${spec.unit})`));
    field.append(label);

    let input;
    if (spec.type === 'select') {
      input = el('select');
      for (const option of spec.options ?? []) {
        const node = el('option', null, String(option).replace(/_/g, ' '));
        node.value = option;
        input.append(node);
      }
    } else {
      input = el('input');
      input.type = 'number';
      if (spec.min !== undefined) input.min = spec.min;
      if (spec.max !== undefined) input.max = spec.max;
      if (spec.step !== undefined) input.step = spec.step;
    }
    input.id = `p-${spec.name}`;
    input.name = spec.name;
    input.value = spec.default ?? '';
    field.append(input);
    if (spec.help) field.append(el('p', 'field__help', spec.help));
    ui.params.append(field);
  }
}

function collectParams() {
  const out = {};
  for (const spec of paramSpecs) {
    const node = $(`p-${spec.name}`);
    if (!node || node.value === '') continue;
    out[spec.name] = spec.type === 'select' ? node.value : Number(node.value);
  }
  return out;
}

// --------------------------------------------------------------- submit ----

ui.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedFile) return;
  closeStream?.();
  ui.submit.disabled = true;
  ui.progressPanel.hidden = false;
  ui.log.textContent = '';
  ui.result.replaceChildren(el('p', 'empty', 'Measuring…'));
  ui.evidenceBlock.hidden = true;
  setProgress(0, 'uploading');
  ui.status.textContent = 'queued';
  try {
    const { job_id: jobId } = await api.submit(selectedFile, collectParams());
    appendLog(`job ${jobId} accepted`);
    follow(jobId);
  } catch (error) {
    failed(error);
  }
});

function setProgress(percent, message) {
  ui.bar.style.width = `${percent}%`;
  ui.bar.setAttribute('aria-valuenow', String(Math.round(percent)));
  ui.percent.textContent = `${Math.round(percent)}%`;
  if (message) ui.message.textContent = message;
}

function appendLog(line) {
  ui.log.textContent += `${line}\n`;
  ui.log.scrollTop = ui.log.scrollHeight;
}

function follow(jobId) {
  closeStream = api.events(jobId, {
    progress: (e) => {
      setProgress(e.percent ?? 0, e.message ?? '');
      if (e.message) appendLog(`${String(Math.round(e.percent ?? 0)).padStart(3)}%  ${e.message}`);
    },
    note: (e) => appendLog(`      ${e.message}`),
    status: (e) => {
      ui.status.textContent = e.status;
      if (e.status === 'done' || e.status === 'failed') finish(jobId, e);
    },
    error: (error) => failed(error),
  });
}

async function finish(jobId, event) {
  setProgress(100, event.status === 'done' ? 'complete' : 'failed');
  ui.submit.disabled = !selectedFile;
  let job;
  try { job = await api.job(jobId); } catch (error) { failed(error); return; }
  if (job.error) { failed(new ApiError(job.error, 500)); return; }
  renderReport(job.result ?? {});
}

function failed(error) {
  ui.submit.disabled = !selectedFile;
  ui.status.textContent = 'failed';
  const notice = el('div', 'notice notice--error');
  notice.append(el('strong', null, error.code ?? 'error'), ' ', error.message ?? String(error));
  ui.result.replaceChildren(notice);
  appendLog(`error: ${error.message ?? error}`);
}

// --------------------------------------------------------------- report ----

const REFUSAL_ADVICE = {
  NO_MARKER: [
    'Print the scale marker from the rail and tape it flat on the same surface as the crack.',
    'Keep the whole marker in shot, in focus, for the frames you care about.',
  ],
  NO_FIDUCIAL: ['Get the whole printed marker into the frame, in focus.'],
  MARKER_TOO_SMALL: [
    'Move closer, or use a larger printed marker.',
    'The marker has to span at least 48 pixels for the scale to be worth anything.',
  ],
  TOO_OBLIQUE: [
    'Stand square to the wall rather than shooting along it.',
    'Foreshortening at this angle costs more resolution across the crack than the measurement can afford.',
  ],
  HIGH_RESIDUAL: ['Flatten the marker against the surface; a curled card does not fit a plane.'],
  INCONSISTENT_SCALE: ['The markers in shot are not coplanar. Use one, or put them all on the same face.'],
  OUT_OF_FOCUS: ['Let the camera focus before you start moving, and walk slower.'],
  MOTION_BLUR: ['Walk slower, or stop at each position and take a still.'],
  UNDER_EXPOSED: ['Add light, or raise the exposure. There is no detail to recover from a black frame.'],
  OVER_EXPOSED: ['Reduce the exposure. Blown highlights cannot be recovered.'],
  CLIPPED: ['Avoid direct sun on the surface, or shade it.'],
  BELOW_RESOLUTION: [
    'Move closer so the crack spans more pixels, or use a longer lens.',
    'A width finer than the blur in the photograph cannot be recovered from it by any method.'],
  EMPTY_INPUT: ['That file did not decode. Try an MP4 or a JPEG.'],
};

function renderReport(record) {
  const results = record.results ?? [];
  const metrics = record.metrics ?? {};
  const refusals = record.refusals ?? [];
  const nodes = [];

  $('stat-frames').textContent = `${metrics.stations ?? 0} of ${metrics.frames_read ?? 0}`;

  const measured = results.filter((r) => r.measurable);
  if (!measured.length) {
    nodes.push(refusalPanel(refusals[0] ?? results.find((r) => r.refusal)?.refusal, metrics, record));
  } else {
    nodes.push(kpiRow(measured, metrics, record));
  }

  if (measured.length) nodes.push(titleBlock(record, metrics));
  if (results.length) nodes.push(scheduleBlock(results, record));
  if (measured.length) nodes.push(distributionBlock(measured, record));
  nodes.push(calibrationBlock(metrics));
  nodes.push(gateBlock(metrics));
  nodes.push(exportBlock(metrics, record));

  ui.result.replaceChildren(...nodes.filter(Boolean));
  renderEvidence(record);
}

function refusalPanel(refusal, metrics, record) {
  const panel = el('section', 'refusal');
  panel.id = 'refusal-panel';
  const head = el('div', 'refusal__head');
  head.append(el('h2', 'refusal__title', 'Cannot measure here'));
  head.append(el('span', 'refusal__code', (refusal?.code ?? 'UNKNOWN').replace(/_/g, ' ').toLowerCase()));
  panel.append(head);
  panel.append(el('p', 'refusal__body',
    refusal?.message ?? 'No frame in this clip can carry a measurement.'));

  const rejected = metrics.frames_rejected ?? {};
  const total = Object.values(rejected).reduce((a, b) => a + b, 0);
  if (total) {
    const summary = Object.entries(rejected)
      .sort((a, b) => b[1] - a[1])
      .map(([code, n]) => `${n} for ${code.replace(/_/g, ' ').toLowerCase()}`)
      .join(', ');
    panel.append(el('p', 'block__note',
      `${total} of ${metrics.frames_read ?? total} frames were rejected: ${summary}.`));
  }

  const next = el('div', 'refusal__next');
  next.append(el('h3', null, 'What would fix it'));
  const list = el('ul');
  for (const line of REFUSAL_ADVICE[refusal?.code] ?? ['Re-shoot with the printed marker in frame.']) {
    list.append(el('li', null, line));
  }
  next.append(list);
  panel.append(next);

  const frame = (record.evidence ?? []).find((e) => e.kind === 'overlay');
  if (frame) {
    const figure = el('figure', 'figure');
    const img = el('img');
    img.src = frame.uri;
    img.alt = 'The frame Hairline declined to measure';
    figure.append(img, el('figcaption', null, frame.caption || ''));
    panel.append(figure);
  }
  return panel;
}

function kpiRow(measured, metrics, record) {
  const row = el('div', 'kpis');
  row.id = 'kpi-row';
  const widest = measured.reduce((a, b) => (b.width_p95_mm > (a?.width_p95_mm ?? -1) ? b : a), null);
  const declined = (record.results ?? []).length - measured.length;
  const scale = (record.metrics?.gates ?? []).find((g) => g.ok);

  const cells = [
    {
      label: 'Widest crack measured',
      value: mm(widest?.width_p95_mm),
      unit: 'mm',
      note: widest ? `${widest.crack_id}, ±${mm(widest.expanded_uncertainty_mm)} mm at k=2` : '',
    },
    { label: 'Crack runs measured', value: String(measured.length), unit: '',
      note: declined ? `${declined} declined` : 'none declined' },
    { label: 'Total crack length', value: mm(metrics.total_length_mm, 0), unit: 'mm',
      note: 'measured on the wall' },
    { label: 'Scale recovered', value: scale ? mm(scale.px_per_mm, 2) : '—', unit: 'px/mm',
      note: scale ? `${(1000 / scale.px_per_mm).toFixed(0)} µm per pixel` : '' },
    { label: 'Stations used', value: `${metrics.stations ?? 0}`, unit: '',
      note: `of ${metrics.frames_read ?? 0} frames read` },
  ];
  for (const cell of cells) {
    const node = el('div', 'kpi');
    node.append(el('div', 'kpi__label', cell.label));
    const value = el('div', 'kpi__value', cell.value);
    if (cell.unit) value.append(' ', el('small', null, cell.unit));
    node.append(value);
    if (cell.note) node.append(el('div', 'kpi__note', cell.note));
    row.append(node);
  }
  return row;
}

function titleBlock(record, metrics) {
  // The drawing sheet's title block: what this survey was, how it was scaled, and a
  // signature line saying what it is not. docs/design/hairline-spec.md section 5.
  const gate = (record.metrics?.gates ?? []).find((g) => g.ok) ?? {};
  const calibration = metrics.calibration ?? {};
  const params = record.params ?? {};
  const block = el('section', 'titleblock');
  const head = el('div', 'titleblock__head');
  head.append(el('span', null, `Survey ${record.run_id ?? ''}`), el('span', null, record.created_at?.slice(0, 19).replace('T', ' ') ?? ''));
  block.append(head);
  const rows = [
    ['Scale reference', `${params.marker_dictionary ?? '—'}, id ${(gate.marker_ids ?? []).join(', ') || '—'}, ${params.marker_length_mm ?? '—'} mm as entered`],
    ['Recovered scale', gate.px_per_mm ? `${gate.px_per_mm.toFixed(3)} px/mm, ${(1000 / gate.px_per_mm).toFixed(0)} µm per pixel` : '—'],
    ['Marker fit', gate.residual_px !== undefined && gate.marker_edge_px ? `${gate.residual_px} px residual on a ${gate.marker_edge_px.toFixed(0)} px edge` : '—'],
    ['Surface off square', gate.apparent_tilt_deg !== undefined && gate.apparent_tilt_deg !== null ? `${gate.apparent_tilt_deg.toFixed(0)}° apparent foreshortening` : '—'],
    ['Stand-off assumed', `${params.working_distance_mm ?? '—'} mm, with ${params.coplanarity_mm ?? '—'} mm of card-to-crack offset`],
    ['Estimator', `${(params.estimator ?? '').replace(/_/g, ' ')}, k=${params.coverage_factor ?? 2}`],
    ['Calibration', calibration.ok ? `passed ${calibration.checked_at}, worst line ${calibration.worst_error_pct}% of ${calibration.tolerance_pct}%` : 'not verified'],
    ['Pipeline', `OpenCV ${record.env?.opencv_version ?? ''} on ${record.env?.machine ?? ''}, build ${record.env?.git_sha ?? ''}`],
  ];
  for (const [name, value] of rows) {
    const row = el('div', 'titleblock__row');
    row.append(el('dt', null, name), el('dd', null, value));
    block.append(row);
  }
  block.append(el('div', 'titleblock__sign',
    'This is a measurement, not a structural assessment. The review bands are an '
    + 'operator setting and are not taken from any design code. An engineer decides.'));
  return block;
}

function calibrationBlock(metrics) {
  // The evidence that travels with every survey, spec section 5.
  const report = metrics.calibration;
  if (!report || !report.lines) return null;
  const block = el('section', 'block');
  const head = el('div', 'block__head');
  head.append(el('h2', 'block__title', 'Calibration evidence'));
  head.append(el('span', 'block__note',
    `this build measured printed lines of known width; worst error ${report.worst_error_pct}% of a ${report.tolerance_pct}% tolerance`));
  block.append(head);
  const table = el('table');
  table.id = 'calibration-table';
  const thead = el('thead');
  const hrow = el('tr');
  for (const text of ['Printed width', 'Measured', 'Error', 'Across the line', 'Verdict']) {
    hrow.append(el('th', text === 'Printed width' || text === 'Verdict' ? '' : 'num', text));
  }
  thead.append(hrow);
  table.append(thead);
  const tbody = el('tbody');
  for (const line of report.lines) {
    const tr = el('tr', line.ok ? '' : 'refused');
    tr.append(el('td', 'id', `${line.printed_mm.toFixed(3)} mm`));
    tr.append(el('td', 'num', line.measured_mm === null ? 'declined' : `${line.measured_mm.toFixed(3)} mm`));
    tr.append(el('td', 'num', line.error_pct === null ? '—' : `${line.error_pct > 0 ? '+' : ''}${line.error_pct.toFixed(2)}%`));
    tr.append(el('td', 'num', line.resolved_px === null ? '—' : `${line.resolved_px.toFixed(1)} px`));
    const verdict = el('td');
    const word = line.measured_mm === null
      ? (line.expected_measurable ? 'missed' : 'correctly declined')
      : (line.ok ? 'within tolerance' : 'out of tolerance');
    verdict.append(el('span', `tag tag--${line.ok ? 'high' : 'none'}`, word));
    tr.append(verdict);
    tbody.append(tr);
  }
  table.append(tbody);
  block.append(table);
  block.append(el('p', 'field__help',
    'The card is rendered, not photographed, so this proves the measurement chain and '
    + 'not a printer. Print the check target from the rail and photograph it to test the '
    + 'rest.'));
  return block;
}

function scheduleBlock(results, record) {
  const block = el('section', 'block');
  const head = el('div', 'block__head');
  head.append(el('h2', 'block__title', 'Crack schedule'));
  head.append(el('span', 'block__note',
    `widths are the 95th percentile along each run, with the expanded uncertainty at k=${record.params?.coverage_factor ?? 2}`));
  block.append(head);

  const table = el('table');
  table.id = 'crack-table';
  const thead = el('thead');
  const hrow = el('tr');
  for (const [text, cls] of [['Run', ''], ['Position on wall', ''], ['Length', 'num'],
    ['Width p50', 'num'], ['Width p95', 'num'], ['Uncertainty', 'num'],
    ['Samples', 'num'], ['Confidence', ''], ['Band', '']]) {
    const th = el('th', cls, text);
    hrow.append(th);
  }
  thead.append(hrow);
  table.append(thead);

  const tbody = el('tbody');
  for (const row of results) {
    const tr = el('tr', row.measurable ? '' : 'refused');
    tr.append(el('td', 'id', row.crack_id));
    tr.append(el('td', null,
      `x ${mm(row.position_mm?.[0], 0)}, y ${mm(row.position_mm?.[1], 0)} mm`));
    tr.append(el('td', 'num', row.length_mm ? `${mm(row.length_mm, 0)} mm` : '—'));
    if (row.measurable) {
      tr.append(el('td', 'num', `${mm(row.width_p50_mm)} mm`));
      tr.append(el('td', 'num', `${mm(row.width_p95_mm)} mm`));
      tr.append(el('td', 'num', `±${mm(row.expanded_uncertainty_mm)} mm`));
    } else {
      const cell = el('td', 'num reason');
      cell.colSpan = 3;
      cell.textContent = row.upper_bound_mm
        ? `cannot measure — finer than ${mm(row.upper_bound_mm)} mm`
        : `cannot measure — ${(row.refusal?.code ?? '').replace(/_/g, ' ').toLowerCase()}`;
      tr.append(cell);
    }
    tr.append(el('td', 'num', String(row.samples ?? 0)));
    const conf = el('td');
    conf.append(el('span', `tag tag--${row.confidence}`, row.confidence));
    tr.append(conf);
    tr.append(el('td', null, row.band));
    tbody.append(tr);
  }
  table.append(tbody);
  block.append(table);
  return block;
}

function distributionBlock(measured, record) {
  const block = el('section', 'block');
  const head = el('div', 'block__head');
  head.append(el('h2', 'block__title', 'Width distribution'));
  head.append(el('span', 'block__note', 'each bar is one crack run; the whisker is ± the expanded uncertainty'));
  block.append(head);

  const bands = record.params?.bands ?? [];
  const sorted = [...measured].sort((a, b) => b.width_p95_mm - a.width_p95_mm);
  const max = Math.max(...sorted.map((r) => r.width_p95_mm + (r.expanded_uncertainty_mm ?? 0)), 0.5) * 1.12;
  const rowH = 30;
  const padL = 96;
  const padR = 86;
  const w = 840;
  const h = sorted.length * rowH + 46;
  const x = (v) => padL + (v / max) * (w - padL - padR);

  const parts = [`<rect width="${w}" height="${h}" fill="#ffffff"/>`];
  for (const band of bands) {
    if (!band.upper_mm || band.upper_mm > max) continue;
    parts.push(
      `<line x1="${x(band.upper_mm).toFixed(1)}" y1="18" x2="${x(band.upper_mm).toFixed(1)}" y2="${h - 24}" stroke="#8A5A06" stroke-width="1" stroke-dasharray="4 3"/>`,
      `<text x="${(x(band.upper_mm) + 4).toFixed(1)}" y="14" font-size="11" fill="#8A5A06" font-family="Barlow Condensed, sans-serif">${band.upper_mm.toFixed(2)} mm</text>`);
  }
  sorted.forEach((row, i) => {
    const y = 26 + i * rowH;
    const bar = x(row.width_p95_mm) - padL;
    const u = row.expanded_uncertainty_mm ?? 0;
    const colour = row.confidence === 'high' ? '#15618F' : '#8A5A06';
    parts.push(
      `<text x="${padL - 8}" y="${y + 13}" text-anchor="end" font-size="12" fill="#12181D" font-family="Barlow Condensed, sans-serif" font-weight="600">${row.crack_id}</text>`,
      `<rect x="${padL}" y="${y + 3}" width="${Math.max(1, bar).toFixed(1)}" height="16" fill="${colour}" fill-opacity="0.85"/>`,
      `<line x1="${x(Math.max(0, row.width_p95_mm - u)).toFixed(1)}" y1="${y + 11}" x2="${x(row.width_p95_mm + u).toFixed(1)}" y2="${y + 11}" stroke="#12181D" stroke-width="1.3"/>`,
      `<line x1="${x(Math.max(0, row.width_p95_mm - u)).toFixed(1)}" y1="${y + 6}" x2="${x(Math.max(0, row.width_p95_mm - u)).toFixed(1)}" y2="${y + 16}" stroke="#12181D" stroke-width="1.3"/>`,
      `<line x1="${x(row.width_p95_mm + u).toFixed(1)}" y1="${y + 6}" x2="${x(row.width_p95_mm + u).toFixed(1)}" y2="${y + 16}" stroke="#12181D" stroke-width="1.3"/>`,
      `<text x="${(x(row.width_p95_mm + u) + 7).toFixed(1)}" y="${y + 16}" font-size="12" fill="#12181D" font-family="Barlow, sans-serif">${row.width_p95_mm.toFixed(2)} ±${u.toFixed(2)}</text>`);
  });
  parts.push(`<line x1="${padL}" y1="${h - 24}" x2="${w - padR}" y2="${h - 24}" stroke="#12181D" stroke-width="1"/>`);
  for (let t = 0; t <= 4; t += 1) {
    const v = (max * t) / 4;
    parts.push(
      `<line x1="${x(v).toFixed(1)}" y1="${h - 24}" x2="${x(v).toFixed(1)}" y2="${h - 19}" stroke="#5E6670"/>`,
      `<text x="${x(v).toFixed(1)}" y="${h - 6}" text-anchor="middle" font-size="11" fill="#5E6670" font-family="Barlow, sans-serif">${v.toFixed(2)}</text>`);
  }

  const wrap = el('div');
  wrap.id = 'width-distribution';
  wrap.innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Crack width distribution">${parts.join('')}</svg>`;
  block.append(wrap);
  return block;
}

function gateBlock(metrics) {
  const rejected = metrics.frames_rejected ?? {};
  if (!Object.keys(rejected).length) return null;
  const block = el('section', 'block');
  const head = el('div', 'block__head');
  head.append(el('h2', 'block__title', 'Frames not used'));
  head.append(el('span', 'block__note',
    `${Object.values(rejected).reduce((a, b) => a + b, 0)} of ${metrics.frames_read} frames`));
  block.append(head);
  const grid = el('div', 'gate');
  grid.id = 'frame-gate';
  for (const [code, n] of Object.entries(rejected).sort((a, b) => b[1] - a[1])) {
    const cell = el('div');
    cell.append(el('b', null, String(n)), ' ', code.replace(/_/g, ' ').toLowerCase());
    grid.append(cell);
  }
  block.append(grid);
  return block;
}

function exportBlock(metrics, record) {
  const block = el('section', 'block');
  const head = el('div', 'block__head');
  head.append(el('h2', 'block__title', 'Export'));
  head.append(el('span', 'block__note', 'refused runs are in the file too, with the reason'));
  block.append(head);
  const row = el('div', 'exports');
  row.id = 'exports';
  if (metrics.schedule_csv) {
    const a = el('a', 'button button--quiet', 'Crack schedule (CSV)');
    a.href = metrics.schedule_csv;
    a.download = 'crack-schedule.csv';
    row.append(a);
  }
  if (metrics.schedule_json) {
    const a = el('a', 'button button--quiet', 'Full run record (JSON)');
    a.href = metrics.schedule_json;
    a.download = 'hairline-run.json';
    row.append(a);
  }
  const print = el('button', 'button button--quiet', 'Print this report');
  print.type = 'button';
  print.addEventListener('click', () => window.print());
  row.append(print);
  block.append(row);
  block.append(el('p', 'field__help',
    `Run ${record.run_id ?? ''} · OpenCV ${record.env?.opencv_version ?? ''} · ${record.env?.machine ?? ''} · build ${record.env?.git_sha ?? ''}`));
  return block;
}

function renderEvidence(record) {
  const items = (record.evidence ?? []).filter((e) => e.uri);
  if (!items.length) { ui.evidenceBlock.hidden = true; return; }
  ui.evidenceBlock.hidden = false;
  ui.evidenceCount.textContent = `${items.length} station${items.length === 1 ? '' : 's'}`;

  const picker = el('div', 'stations');
  picker.id = 'station-picker';
  const figure = el('figure', 'figure');
  figure.id = 'overlay-figure';
  const img = el('img');
  img.id = 'overlay-image';
  const caption = el('figcaption');
  figure.append(img, caption);

  const show = (i) => {
    img.src = items[i].uri;
    img.alt = `Station ${i + 1} with the measurement ladder drawn on each crack`;
    caption.textContent = items[i].caption || items[i].label;
    [...picker.children].forEach((b, j) => b.setAttribute('aria-pressed', String(i === j)));
  };
  items.forEach((item, i) => {
    const button = el('button', null, item.label);
    button.type = 'button';
    button.addEventListener('click', () => show(i));
    picker.append(button);
  });
  ui.evidence.replaceChildren(picker, figure);
  show(0);
}

// ----------------------------------------------------------------- boot ----

async function runSample(entry) {
  const response = await fetch(`/api/samples/${entry.file}`);
  const blob = await response.blob();
  setFile(new File([blob], entry.file, { type: blob.type }), `${entry.title}`);
  ui.form.requestSubmit();
}

(async function boot() {
  loadCalibration();
  try {
    const [config, version] = await Promise.all([api.config(), api.version()]);
    document.title = `${config.product.title} — crack survey`;
    $('product-version').textContent = `v${config.product.version}`;
    $('product-tagline').textContent = config.product.tagline ?? '';
    paramSpecs = config.params ?? [];
    renderParams(paramSpecs);
    ui.hint.textContent = `Accepts ${config.accepts.join(' ')} up to ${Math.round(config.max_upload_bytes / (1024 * 1024))} MB`;
    ui.fileInput.accept = config.accepts.join(',');
    $('stat-opencv').textContent = version.opencv_version;
    $('stat-hal').textContent = version.opencv_build?.kleidicv ? 'KleidiCV' :
      (version.opencv_build?.ipp ? 'Intel IPP' : (version.opencv_build?.custom_hal ?? 'none'));
    $('stat-machine').textContent = `${version.machine} · ${version.opencv_build?.threads ?? '?'} threads`;
    $('stat-sha').textContent = version.git_sha;
    $('stat-instance').textContent = version.machine === 'aarch64' ? 'AWS Graviton' : version.platform.split('-')[0];
  } catch (error) {
    ui.hint.textContent = 'Could not load the service configuration.';
    console.error(error);
  }
  try {
    const { samples = [] } = await (await fetch('/api/samples')).json();
    if (!samples.length) { ui.samples.replaceChildren(el('span', 'field__help', 'None bundled.')); return; }
    ui.samples.replaceChildren();
    for (const entry of samples) {
      const button = el('button');
      button.type = 'button';
      button.id = `sample-${entry.file.replace(/\W+/g, '-')}`;
      button.append(el('b', null, entry.title), el('span', null, entry.expect));
      button.addEventListener('click', () => runSample(entry));
      ui.samples.append(button);
    }
  } catch (error) {
    ui.samples.replaceChildren(el('span', 'field__help', 'Samples unavailable.'));
  }
})();
