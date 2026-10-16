// Ejecutar con Node.js: node docs/diagnostico-ui/comprobar-interfaz.mjs
// Evalúa el JavaScript original con DOM y HTTP simulados; no es una prueba de navegador.
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const snapshot = 'a942ed8:coordinator/static/index.html (diagnóstico histórico)';
const html = execFileSync('git', ['show', 'a942ed8:coordinator/static/index.html'], {
  cwd: fileURLToPath(new URL('../../', import.meta.url)), encoding: 'utf8', windowsHide: true,
});
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace('refresh(); setInterval(refresh, 4000);', '');
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    textContent: '', innerHTML: '', className: '', title: '',
    getBoundingClientRect: () => ({left: 0, top: 0, width: 100, bottom: 20}),
  });
  return elements.get(selector);
};
const context = vm.createContext({
  document: {querySelector: element, addEventListener() {}},
  location: {host: 'diagnostic.invalid'}, addEventListener() {},
  setTimeout() {}, clearTimeout() {}, console,
});
vm.runInContext(source, context);
const file = {filename: 'audit.txt', size_bytes: 4, sha256: '0'.repeat(64),
  created_at: '2026-10-15T00:00:00Z', primary_node: 'node1', replica_node: 'node2'};
context.fixture = {status: [], files: {'audit.txt': file}, events: [], cat: null,
  real: {node1: {reachable: true, files: [file], source: 'catalog'},
    node2: {reachable: true, files: [file], source: 'catalog'}}};
vm.runInContext('state = fixture', context);
const output = {snapshot, catalog_only_health: vm.runInContext('fileHealth(state.files["audit.txt"])', context)};

context.fetch = async url => ({ok: url !== '/status', statusText: 'Service Unavailable',
  json: async () => url === '/status' ? {detail: 'probe failed'} :
    url === '/files' ? {files: {}} : url === '/nodes/files' ? {nodes: {}} : {events: []}});
await vm.runInContext('refresh()', context);
output.status_failure_badge = element('#conn').textContent;

const pending = [];
let generation = 'old';
context.fetch = url => new Promise(resolve => pending.push({url, generation, resolve}));
function finish(generation) {
  for (const item of pending.filter(p => p.generation === generation)) {
    const body = item.url === '/status' ? {nodes: [{node_id: generation, status: 'UP'}]} :
      item.url === '/files' ? {files: {}} : item.url === '/nodes/files' ? {nodes: {}} :
      item.url === '/events' ? {events: []} : {};
    item.resolve({ok: true, json: async () => body});
  }
}
const older = vm.runInContext('refresh()', context);
generation = 'new';
const newer = vm.runInContext('refresh()', context);
finish('new'); await newer;
output.after_newer_response = vm.runInContext('state.status[0].node_id', context);
finish('old'); await older;
output.after_older_response = vm.runInContext('state.status[0].node_id', context);
console.log(JSON.stringify(output, null, 2));
await fs.writeFile(new URL('08-interfaz.json', import.meta.url), JSON.stringify(output, null, 2) + '\n');
