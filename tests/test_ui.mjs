// Pruebas del JavaScript real con DOM/HTTP controlados. Ejecutar: node tests/test_ui.mjs
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const html = await fs.readFile(new URL('../coordinator/static/index.html', import.meta.url), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\npoll\(\);\s*$/, '');
function setup() {
  const elements = new Map(), timers = new Map();
  let timerId = 0;
  let clickHandler;
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {textContent: '', innerHTML: '', className: '', title: '',
      getBoundingClientRect: () => ({left: 0, top: 0, width: 100, bottom: 20})});
    return elements.get(selector);
  };
  const context = vm.createContext({document: {querySelector: element, addEventListener(type, handler) {
    if (type === 'click') clickHandler = handler;
  }},
    location: {host: 'test.invalid'}, addEventListener() {}, confirm: () => true, AbortController, console,
    setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(source, context);
  return {context, element, timers, click: event => clickHandler(event),
    run: code => vm.runInContext(code, context)};
}
const file = {filename: 'audit.txt', storage_key: 'key.txt', size_bytes: 4, sha256: '0'.repeat(64),
  status: 'COMMITTED', created_at: '2026-10-15T00:00:00Z', primary_node: 'node1', replica_node: 'node2'};
const copy = (id='node1') => ({reachable: true, source: 'disk', storage_id: id, files: [{...file}]});
const fixture = () => ({status: [], files: {'audit.txt': {...file}}, events: [], cat: null,
  real: {node1: copy('node1'), node2: copy('node2')}});
const body = url => url.startsWith('/status') ? {nodes: []} : url === '/files' ? {files: {'audit.txt': file}} :
  url === '/nodes/files' ? {nodes: {node1: copy('node1'), node2: copy('node2')}} : url === '/events' ? {events: []} : {};
const response = data => ({ok: true, json: async () => data});
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log('OK', name); }

await test('integridad requiere disco, clave, tamaño y SHA-256', () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).label'), 'Verificado 2/2');
  t.run('state.real.node1.source = "catalog"');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).copies'), 1);
  t.run('state.real.node1.source = "disk"; state.real.node1.files[0].sha256 = "corrupt"');
  assert.match(t.run('fileHealth(state.files["audit.txt"]).label'), /corrupta/);
  t.run('state.real.node1.files = []; state.real.node2.files = []');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).label'), 'Ausente 0/2');
  t.run('state.real.node1.reachable = false');
  assert.match(t.run('fileHealth(state.files["audit.txt"]).label'), /sin verificar/);
});
await test('un almacenamiento sustituido no acredita la copia original', () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture');
  t.run('state.files["audit.txt"].storage_ids = {node1: "expected"}; state.real.node1.storage_id = "other"');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).p'), null);
});
await test('el mismo almacenamiento no cuenta como dos copias', () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture; state.real.node2.storage_id = state.real.node1.storage_id');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).copies'), 1);
});
await test('fallar solo status no muestra desconexión del coordinador', async () => {
  const t = setup();
  t.context.fetch = async url => url === '/status'
    ? {ok: false, status: 503, json: async () => ({detail: 'probe failed'})} : response(body(url));
  assert.equal(await t.run('refresh()'), false);
  assert.match(t.element('#conn').textContent, /en línea/);
  assert.match(t.element('#conn').textContent, /incompleta/);
});
await test('no se conserva una integridad antigua si falla el inventario', async () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture');
  t.context.fetch = async url => { if (url === '/nodes/files') throw new Error('network'); return response(body(url)); };
  await t.run('refresh()');
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).copies'), 0);
  assert.match(t.run('fileHealth(state.files["audit.txt"]).label'), /sin verificar/);
});
await test('fallo de todas las consultas muestra desconexión y datos sin verificar', async () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture');
  t.context.fetch = async () => { throw new Error('network'); };
  await t.run('refresh()');
  assert.match(t.element('#conn').textContent, /Sin conexión/);
  assert.equal(t.run('fileHealth(state.files["audit.txt"]).copies'), 0);
});
await test('refrescos simultáneos comparten peticiones y no pueden retroceder', async () => {
  const t = setup(), pending = [];
  t.context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
  const first = t.run('refresh()'), second = t.run('refresh()');
  assert.equal(first, second); assert.equal(pending.length, 5);
  for (const item of pending) item.resolve(response(body(item.url)));
  assert.equal(await first, true); await second;
  assert.equal(pending.length, 5);
});
await test('consulta forzada durante refresco se ejecuta después, una sola vez', async () => {
  const t = setup(), pending = [];
  t.context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
  const first = t.run('refresh()'); t.run('refresh(true)'); t.run('refresh(true)');
  assert.equal(pending.length, 5);
  for (const item of pending.slice()) item.resolve(response(body(item.url)));
  for (let i = 0; i < 30 && pending.length < 10; i++) await Promise.resolve();
  assert.equal(pending.length, 10);
  assert.equal(pending[5].url, '/status');
  for (const item of pending.slice(5)) item.resolve(response(body(item.url)));
  assert.equal(await first, true); assert.equal(pending.length, 10);
});
await test('una eliminación pendiente ofrece reintentar y bloquea descargar', () => {
  const t = setup(); t.context.fixture = fixture(); t.run('state = fixture; state.files["audit.txt"].status = "DELETING"; render()');
  assert.match(t.element('#rows').innerHTML, /Eliminación pendiente/);
  assert.match(t.element('#rows').innerHTML, /Reintentar eliminación/);
  assert.match(t.element('#rows').innerHTML, /data-dl="audit.txt" disabled/);
  assert.equal(t.element('#s-health').textContent, '0 / 1');
});
await test('fallo de descarga desde un elemento interno del botón muestra aviso', async () => {
  const t = setup();
  const button = {dataset: {dl: 'audit.txt'}, disabled: false};
  t.context.fetch = async url => url.startsWith('/download/')
    ? {ok: false, status: 503, json: async () => ({detail: 'Ninguna de las dos copias está disponible'})}
    : response(body(url));
  await t.click({target: {closest: () => button}});
  assert.equal(t.element('#toast').textContent, 'Ninguna de las dos copias está disponible');
  assert.match(t.element('#toast').className, /bad/);
});
await test('HTTP tiene plazo y libera el temporizador', async () => {
  const t = setup();
  t.context.fetch = (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); });
  });
  const request = t.run('j("/files")');
  const check = assert.rejects(request, /tiempo de espera/);
  [...t.timers.values()].find(timer => timer.delay === 15000).fn();
  await check; assert.equal(t.timers.size, 0);
});
console.log(`${passed} pruebas de interfaz correctas`);
