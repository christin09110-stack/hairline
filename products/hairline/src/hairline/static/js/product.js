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

  const banner = el('section', 'notpublishing');
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
  manual.load(file);
  refreshSubmit();
}

// The marker flow only needs a file. The ruler flow also needs the two points and the
// length between them, because without those there is no scale at all.
function refreshSubmit() {
  ui.submit.disabled = !selectedFile || (manual.on && !manual.ready());
}

// ----------------------------------------------------------- manual scale ----
// For photographs that carry a ruler or crack gauge instead of the printed marker.
// Everything is kept as fractions of the photograph's natural size, which is what
// SurveyParams.manual_scale and exclude_regions expect, so the canvas can be any size.

const manual = (() => {
  const toggle = $('manual-on');
  const panel = $('manual-panel');
  const canvas = $('manual-canvas');
  const stage = $('manual-stage');
  const status = $('manual-status');
  const length = $('manual-length');
  const tilt = $('manual-tilt');
  const expectedWidth = $('ms-expected-width');
  const edge = $('manual-edge');
  const rough = $('manual-rough');
  const tools = { scale: $('tool-scale'), exclude: $('tool-exclude') };
  const ctx = canvas.getContext('2d');

  const state = { on: false, image: null, url: null, points: [], rects: [], tool: 'scale', drag: null };
  const round = (v) => Math.round(Math.min(1, Math.max(0, v)) * 1e5) / 1e5;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function setTool(name) {
    state.tool = name;
    for (const [key, button] of Object.entries(tools)) button.setAttribute('aria-pressed', String(key === name));
  }

  function lengthMm() {
    const v = Number(length.value);
    return length.value !== '' && Number.isFinite(v) && v > 0 ? v : null;
  }

  function ready() {
    return Boolean(state.image) && state.points.length === 2 && lengthMm() !== null;
  }

  function describe() {
    if (!selectedFile) return 'Choose a photograph first.';
    if (!state.image) return 'This works on a photograph, not a video. Choose a JPEG or PNG.';
    if (state.points.length < 2) {
      return state.points.length ? 'Now click the second point on the ruler.' : 'Click two points on the ruler, as far apart as you can read.';
    }
    if (lengthMm() === null) return 'Enter the length between the two points.';
    const leaveOuts = state.rects.length ? `${state.rects.length} area${state.rects.length === 1 ? '' : 's'} left out` : 'drag a box over the ruler to leave it out';
    return `Scale set · ${leaveOuts}`;
  }

  function sync() {
    status.textContent = describe();
    draw();
    refreshSubmit();
  }

  function layout() {
    if (!state.image) return;
    const { naturalWidth: nw, naturalHeight: nh } = state.image;
    const maxW = stage.clientWidth - 2;
    const maxH = Math.max(240, window.innerHeight * 0.7);
    const scale = Math.min(maxW / nw, maxH / nh);
    const w = Math.max(1, Math.floor(nw * scale));
    const h = Math.max(1, Math.floor(nh * scale));
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    draw();
  }

  function draw() {
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    if (!state.image) return;
    ctx.drawImage(state.image, 0, 0, W, H);
    const unit = W / parseFloat(canvas.style.width || W);
    const accent = css('--accent') || '#15618f';
    const signal = css('--signal') || '#a8321f';
    const font = `600 ${13 * unit}px Barlow, sans-serif`;

    const label = (text, x, y, colour) => {
      ctx.font = font;
      const pad = 4 * unit;
      const tw = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(255,255,255,0.92)';
      ctx.fillRect(x, y - 15 * unit, tw + pad * 2, 19 * unit);
      ctx.fillStyle = colour;
      ctx.fillText(text, x + pad, y);
    };

    const rects = state.drag ? [...state.rects, state.drag] : state.rects;
    rects.forEach((r, i) => {
      const x = Math.min(r.x1, r.x2) * W;
      const y = Math.min(r.y1, r.y2) * H;
      const w = Math.abs(r.x2 - r.x1) * W;
      const h = Math.abs(r.y2 - r.y1) * H;
      ctx.fillStyle = 'rgba(168, 50, 31, 0.18)';
      ctx.fillRect(x, y, w, h);
      ctx.setLineDash([6 * unit, 4 * unit]);
      ctx.lineWidth = 2 * unit;
      ctx.strokeStyle = signal;
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      if (i < state.rects.length) label('left out', x + 4 * unit, y + 18 * unit, signal);
    });

    const pts = state.points.map((p) => [p.x * W, p.y * H]);
    if (pts.length === 2) {
      ctx.lineWidth = 2.5 * unit;
      ctx.strokeStyle = accent;
      ctx.beginPath();
      ctx.moveTo(...pts[0]);
      ctx.lineTo(...pts[1]);
      ctx.stroke();
      const mid = [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2];
      const mmText = lengthMm() === null ? 'length? mm' : `${lengthMm()} mm`;
      label(mmText, mid[0] + 6 * unit, mid[1] - 10 * unit, accent);
    }
    for (const [x, y] of pts) {
      ctx.lineWidth = 2 * unit;
      ctx.strokeStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(x, y, 7 * unit, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = accent;
      ctx.beginPath();
      ctx.arc(x, y, 5.5 * unit, 0, Math.PI * 2);
      ctx.moveTo(x - 11 * unit, y); ctx.lineTo(x + 11 * unit, y);
      ctx.moveTo(x, y - 11 * unit); ctx.lineTo(x, y + 11 * unit);
      ctx.stroke();
    }
  }

  function at(event) {
    const box = canvas.getBoundingClientRect();
    return { x: round((event.clientX - box.left) / box.width), y: round((event.clientY - box.top) / box.height) };
  }

  canvas.addEventListener('pointerdown', (event) => {
    if (!state.image) return;
    event.preventDefault();
    const p = at(event);
    if (state.tool === 'scale') {
      // A third click starts a new pair rather than guessing which point to move.
      state.points = state.points.length >= 2 ? [p] : [...state.points, p];
      sync();
      if (state.points.length === 2 && lengthMm() === null) length.focus();
      return;
    }
    canvas.setPointerCapture(event.pointerId);
    state.drag = { x1: p.x, y1: p.y, x2: p.x, y2: p.y };
    draw();
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!state.drag) return;
    const p = at(event);
    state.drag.x2 = p.x;
    state.drag.y2 = p.y;
    draw();
  });
  const endDrag = (event) => {
    if (!state.drag) return;
    const p = at(event);
    const r = state.drag;
    r.x2 = p.x; r.y2 = p.y;
    state.drag = null;
    if (Math.abs(r.x2 - r.x1) > 0.005 && Math.abs(r.y2 - r.y1) > 0.005) state.rects.push(r);
    sync();
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', () => { state.drag = null; draw(); });

  tools.scale.addEventListener('click', () => setTool('scale'));
  tools.exclude.addEventListener('click', () => setTool('exclude'));
  $('manual-undo').addEventListener('click', () => { state.rects.pop(); sync(); });
  $('manual-reset').addEventListener('click', () => { state.points = []; state.rects = []; setTool('scale'); sync(); });
  length.addEventListener('input', sync);
  window.addEventListener('resize', layout);

  function setOn(on) {
    state.on = on;
    toggle.checked = on;
    panel.hidden = !on;
    $('manual-help').hidden = !on;
    if (on) layout();
    sync();
  }
  toggle.addEventListener('change', () => setOn(toggle.checked));

  function load(file) {
    if (state.url) URL.revokeObjectURL(state.url);
    state.image = null;
    state.url = null;
    state.points = [];
    state.rects = [];
    state.drag = null;
    if (file && /^image\//.test(file.type)) {
      state.url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { if (state.url && img.src === state.url) { state.image = img; layout(); sync(); } };
      img.src = state.url;
    }
    if (ctx) sync();
  }

  function params() {
    if (!ready()) return {};
    const [a, b] = state.points;
    const out = {
      manual_scale: [a.x, a.y, b.x, b.y, lengthMm()],
      keep_edge_cracks: edge.checked,
      segmentation: rough.checked ? 'blackhat' : 'adaptive',
    };
    const t = Number(tilt.value);
    if (tilt.value !== '' && Number.isFinite(t)) out.manual_scale_max_tilt_deg = t;
    const w = Number(expectedWidth.value);
    if (expectedWidth.value !== '' && Number.isFinite(w) && w > 0) out.expected_width_mm = w;
    if (state.rects.length) {
      out.exclude_regions = state.rects.map((r) => {
        const x0 = Math.min(r.x1, r.x2); const x1 = Math.max(r.x1, r.x2);
        const y0 = Math.min(r.y1, r.y2); const y1 = Math.max(r.y1, r.y2);
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
      });
    }
    return out;
  }

  return {
    get on() { return state.on; },
    ready, load, params, setOn,
  };
})();

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
    const params = manual.on ? { ...collectParams(), ...manual.params() } : collectParams();
    const { job_id: jobId } = await api.submit(selectedFile, params);
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
  refreshSubmit();
  let job;
  try { job = await api.job(jobId); } catch (error) { failed(error); return; }
  if (job.error) { failed(new ApiError(job.error, 500)); return; }
  renderReport(job.result ?? {});
}

function failed(error) {
  refreshSubmit();
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
  NO_CRACK_FOUND: [
    'Check that no leave-out box covers the crack itself.',
    'If the crack runs past the edge of the photo, tick "Crack runs off the photo".',
    'Try with "Rough real surface" switched the other way, or lower "Shortest crack to report" in Survey settings.',
  ],
};

function renderReport(record) {
  const results = record.results ?? [];
  const metrics = record.metrics ?? {};
  const refusals = record.refusals ?? [];
  const nodes = [];

  $('stat-frames').textContent = `${metrics.stations ?? 0} of ${metrics.frames_read ?? 0}`;

  const measured = results.filter((r) => r.measurable);
  if (metrics.scale_source === 'manual') nodes.push(manualScaleNotice(record));
  if (!measured.length) {
    let refusal = refusals[0] ?? results.find((r) => r.refusal)?.refusal;
    if (!refusal && metrics.scale_source === 'manual' && !results.length) {
      // The scale was accepted and nothing crack-like survived segmentation. Saying
      // "re-shoot with the printed marker" here would point at the wrong problem.
      refusal = {
        code: 'NO_CRACK_FOUND',
        message: `The scale from the two points was accepted (${mm((metrics.gates ?? []).find((g) => g.ok)?.px_per_mm, 2)} px/mm), but no crack long and dark enough to measure was found outside the areas left out.`,
      };
    }
    nodes.push(refusalPanel(refusal, metrics, record));
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

function manualScaleNotice(record) {
  // A ruler clicked by hand is a weaker reference than the printed marker, and the
  // report has to say so where it cannot be missed, not only in the JSON.
  const tilt = record.params?.manual_scale_max_tilt_deg ?? 10;
  const notice = el('div', 'notice');
  notice.id = 'manual-scale-note';
  notice.append(el('strong', null,
    `Scale from two points on a reference, not the printed marker. Wider uncertainty; surface assumed within ${Number(tilt).toFixed(0)}° of square.`));
  const warnings = record.warnings ?? [];
  if (warnings.length) {
    const list = el('ul');
    list.id = 'run-warnings';
    for (const line of warnings) list.append(el('li', null, line));
    notice.append(list);
  }
  return notice;
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
  const manualScale = gate.scale_source === 'manual';
  const rows = [
    manualScale
      ? ['Scale reference', `two points on a reference in the photo, ${params.manual_scale?.[4] ?? '—'} mm apart as entered (no printed marker)`]
      : ['Scale reference', `${params.marker_dictionary ?? '—'}, id ${(gate.marker_ids ?? []).join(', ') || '—'}, ${params.marker_length_mm ?? '—'} mm as entered`],
    ['Recovered scale', gate.px_per_mm ? `${gate.px_per_mm.toFixed(3)} px/mm, ${(1000 / gate.px_per_mm).toFixed(0)} µm per pixel` : '—'],
    manualScale
      ? ['Point accuracy', gate.marker_edge_px ? `±${gate.residual_px ?? '—'} px per point on a ${gate.marker_edge_px.toFixed(0)} px span` : '—']
      : ['Marker fit', gate.residual_px !== undefined && gate.marker_edge_px ? `${gate.residual_px} px residual on a ${gate.marker_edge_px.toFixed(0)} px edge` : '—'],
    manualScale
      ? ['Surface off square', `not measured; assumed within ${params.manual_scale_max_tilt_deg ?? 10}° and charged to the uncertainty`]
      : ['Surface off square', gate.apparent_tilt_deg !== undefined && gate.apparent_tilt_deg !== null ? `${gate.apparent_tilt_deg.toFixed(0)}° apparent foreshortening` : '—'],
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
  // The bundled samples carry the printed marker, so they always run the marker flow.
  manual.setOn(false);
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
