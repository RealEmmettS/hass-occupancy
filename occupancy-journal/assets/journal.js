const $ = (selector) => document.querySelector(selector);
const dateTime = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'No record';
const dateOnly = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'}) : 'No record';
const number = (value) => Number(value || 0).toLocaleString();
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function stat(label, value, note) {
  const root = el('dl', undefined, 'stat');
  const detail = el('dd');
  detail.append(el('strong', value), el('p', note));
  root.append(el('dt', label), detail);
  return root;
}
function cell(row, node, label, header, className) {
  const td = el('td', undefined, className);
  td.setAttribute('headers', header);
  if (label) td.dataset.label = label;
  td.append(node);
  row.append(td);
}
function render(data) {
  const runtime = data.runtime || {};
  const age = runtime.last_cycle ? (Date.now() - Date.parse(runtime.last_cycle)) / 1000 : Infinity;
  const errors = Object.keys(runtime.errors || {});
  const healthy = age < 180 && errors.length === 0;
  $('#collection-status').dataset.state = healthy ? 'healthy' : !runtime.last_cycle && !errors.length ? 'pending' : 'attention';
  $('#message').textContent = !runtime.last_cycle && !errors.length ? 'Waiting for the first sample.'
    : `${healthy ? 'Collecting' : 'Collection needs attention'}.${runtime.last_cycle ? ' Last sample ' + dateTime(runtime.last_cycle) + '.' : ''}${errors.length ? ' Check collection details for source errors.' : ''}`;
  const residents = data.residents || {};
  const members = data.forecast_members || [];
  $('#coverage').textContent = Object.keys(residents).length ? `${members.length} of ${Object.keys(residents).length} residents are included in forecasts.` : 'No residents configured yet.';
  const outside = Object.keys(residents).filter(name => !members.includes(name));
  $('#coverage-detail').textContent = `Included: ${members.join(', ') || 'none'}.${outside.length ? ' Outside the forecast: ' + outside.join(', ') + '.' : ''}`;
  const link = $('#forecast-link');
  // Ingress paths are local, never an arbitrary URL from a captured record.
  if (/^\/[a-z0-9_]+$/.test(data.forecast_panel || '')) { link.href = data.forecast_panel; link.hidden = false; }
  const history = data.observation_history || {};
  const expected = history.expected_samples_24h || 1440;
  const coverage = Math.min(100, 100 * (history.samples_last_24h || 0) / expected);
  const elapsedDays = (Date.parse(history.last) - Date.parse(history.first)) / 86400000;
  const span = !Number.isFinite(elapsedDays) ? 'No records' : elapsedDays < 1 ? 'Less than a day' : `${Math.floor(elapsedDays)} days`;
  $('#stats').replaceChildren(
    stat('History span', span, history.first ? `${dateOnly(history.first)} to ${dateOnly(history.last)}` : 'Waiting for the first observation'),
    stat('Last 24 hours', `${coverage.toFixed(1)}%`, `${number(history.samples_last_24h)} of ${number(expected)} expected samples`),
    stat('Forecast snapshots', number(data.counts?.forecast), `${number(data.retention_days)} days of retention`)
  );
  const observations = data.latest?.observations?.value || {};
  const rows = [];
  for (const [name, id] of Object.entries(residents)) {
    const state = observations[id];
    const check = data.tracker_checks?.[name];
    const row = el('tr');
    const identity = el('span', name, 'resident-name');
    identity.append(el('span', members.includes(name) ? 'Included in forecasts' : 'Observation only', 'resident-scope'));
    cell(row, identity, '', 'col-resident');
    const known = state && !['unknown', 'unavailable', ''].includes(state.state);
    const label = !state ? 'No entity' : !known ? 'Unknown' : state.state === 'home' ? 'Home' : state.state === 'not_home' ? 'Away' : state.state;
    cell(row, el('span', label, 'state-label ' + (!known ? 'unknown' : state.state === 'home' ? 'home' : 'away')), 'Reported state', 'col-state', 'state-cell');
    const agreement = check?.disagree ? 'Trackers disagree' : check?.available > 1 ? 'Trackers agree' : check?.available === 1 ? 'One available tracker' : check?.entity_present ? 'No available tracker' : 'Entity missing';
    cell(row, el('span', agreement, check?.disagree ? 'attention' : ''), 'Tracker agreement', 'col-agreement');
    cell(row, el('span', dateTime(state?.last_updated)), 'State updated', 'col-updated');
    rows.push(row);
  }
  $('#people').replaceChildren(...rows);
  if (!rows.length) {
    const row = el('tr'); const td = el('td', 'No residents configured.'); td.colSpan = 4; row.append(td); $('#people').append(row);
  }
  const cooling = [];
  for (const [id, state] of Object.entries(observations)) {
    if (!id.startsWith('climate.')) continue;
    const attrs = state.attributes || {};
    const row = el('div', undefined, 'cooling-row');
    row.append(el('h3', attrs.friendly_name || id));
    const values = ['unknown','unavailable'].includes(state.state) ? ['No current reading'] : [
      state.state,
      attrs.current_temperature != null ? `Room ${attrs.current_temperature}°` : '',
      attrs.temperature != null ? `Target ${attrs.temperature}°` : '',
      attrs.current_humidity != null ? `${attrs.current_humidity}% humidity` : '',
      attrs.hvac_action || '',
    ].filter(Boolean);
    row.append(el('p', values.join(', '))); cooling.push(row);
  }
  $('#cooling').replaceChildren(...(cooling.length ? cooling : [el('p', 'No cooling observations recorded.', 'muted')]));
  const retention = Math.max(1, Math.min(365, Number(data.retention_days) || 90));
  $('#full-export').href = `./export-view?days=${retention}`;
  $('#full-export').textContent = `View up to ${retention} days`;
  $('#retention-note').textContent = `${number(data.counts?.observations)} observations stored. ${(Number(data.bytes || 0) / 1048576).toFixed(1)} MiB on disk.`;
  $('#forecast').textContent = JSON.stringify(data.latest?.forecast ?? null, null, 2);
  $('#details').textContent = JSON.stringify({first_record:data.first_record,last_record:data.last_record,counts:data.counts,runtime,forecast_status:data.latest?.forecast_status}, null, 2);
  $('#version').textContent = data.version ? `v${data.version}` : '';
}
let loading = false;
let loaded = false;
async function refresh() {
  if (loading) return;
  loading = true;
  const button = $('#refresh');
  button.disabled = true; button.textContent = 'Refreshing…';
  try {
    const response = await fetch('./api/status', {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    loaded = true;
  } catch (error) {
    $('#collection-status').dataset.state = 'attention';
    $('#message').textContent = `Unable to refresh the journal. ${error.message}. ${loaded ? 'Last loaded readings remain below. ' : ''}Try Refresh again.`;
    if (!loaded) {
      $('#stats').replaceChildren(el('p', 'Collection summary is unavailable until the connection recovers.', 'muted'));
      $('#coverage').textContent = 'Forecast coverage could not be loaded.';
      $('#cooling').replaceChildren(el('p', 'Readings are unavailable until the connection recovers.', 'muted'));
    }
  } finally {
    loading = false; button.disabled = false; button.textContent = 'Refresh';
  }
}
$('#refresh').addEventListener('click', refresh);
refresh();
setInterval(refresh, 60000);
