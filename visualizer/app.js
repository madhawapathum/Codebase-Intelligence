import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const NODE_STYLE = {
  class: { color: 0x4f8ef7, kind: 'sphere', scale: 1.0 },
  function: { color: 0x43a047, kind: 'sphere', scale: 0.8 },
  method: { color: 0x26a69a, kind: 'sphere', scale: 0.8 },
  module: { color: 0xffb74d, kind: 'box', scale: 1.0 },
  file: { color: 0x90a4ae, kind: 'box', scale: 0.9 },
  repository: { color: 0xffffff, kind: 'box', scale: 1.2 },
  database_table: { color: 0xe57373, kind: 'cylinder', scale: 1.0 },
  configuration: { color: 0xba68c8, kind: 'octahedron', scale: 1.0 },
  test: { color: 0xa1887f, kind: 'sphere', scale: 0.8 },
  external_or_unresolved: { color: 0x607d8b, kind: 'sphere', scale: 0.55 },
};

const EDGE_COLOR = {
  calls: 0x90a4ae, imports: 0x4fc3f7, inherits: 0xab47bc, decorated_by: 0xffca28,
  tests: 0xa1887f, defines_table: 0xe53935, reads_table: 0xef5350, writes_table: 0xff7043,
  queries: 0x7e57c2, reads_config: 0x26c6da, writes_config: 0x00bcd4,
  writes: 0xff8a65, updates: 0xffa726, deletes: 0xff5252,
};
const DEFAULT_EDGE_COLOR = 0x5c6670;

const state = { repository: null, nodes: [], edges: [], positions: new Map(), selected: null };

const container = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1017);
const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 2000);
camera.position.set(0, 0, 18);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
dirLight.position.set(5, 10, 7);
scene.add(dirLight);

const graphGroup = new THREE.Group();
scene.add(graphGroup);

const raycaster = new THREE.Raycaster();
raycaster.params.Line = { threshold: 0.15 };
const pointer = new THREE.Vector2();

function resize() {
  const w = container.clientWidth, h = container.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
animate();

function vec(p) { return new THREE.Vector3(p.x, p.y, p.z); }

function shortName(node) {
  if (node.name && node.type !== 'external_or_unresolved') return node.name;
  const parts = String(node.id).split('.');
  return parts[parts.length - 1];
}

function nodeStyle(node) {
  if (node.is_test) return NODE_STYLE.test;
  return NODE_STYLE[node.type] || NODE_STYLE.external_or_unresolved;
}

function makeNodeMesh(node) {
  const style = nodeStyle(node);
  let geometry;
  if (style.kind === 'box') geometry = new THREE.BoxGeometry(1, 1, 1);
  else if (style.kind === 'cylinder') geometry = new THREE.CylinderGeometry(0.5, 0.5, 0.9, 20);
  else if (style.kind === 'octahedron') geometry = new THREE.OctahedronGeometry(0.7);
  else geometry = new THREE.SphereGeometry(0.5, 24, 16);
  const material = new THREE.MeshLambertMaterial({ color: style.color });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.scale.setScalar(style.scale);
  mesh.userData.kind = 'node';
  mesh.userData.node = node;
  return mesh;
}

function makeLabel(text) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 96;
  const ctx = canvas.getContext('2d');
  let fontSize = 40;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#e6e8ee';
  while (fontSize > 14) {
    ctx.font = `bold ${fontSize}px sans-serif`;
    if (ctx.measureText(text).width <= 472) break;
    fontSize -= 2;
  }
  ctx.fillText(text, 256, 48);
  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({ map: texture, depthWrite: false, transparent: true });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(3.2, 0.6, 1);
  sprite.raycast = () => {};
  return sprite;
}
function computeLayout(nodes, edges) {
  const positions = new Map();
  const n = nodes.length;
  const golden = Math.PI * (3 - Math.sqrt(5));
  nodes.forEach((node, i) => {
    if (n === 1) { positions.set(node.id, { x: 0, y: 0, z: 0 }); return; }
    const t = i / (n - 1);
    const y = 1 - 2 * t;
    const r = Math.sqrt(Math.max(1 - y * y, 0.001));
    const theta = golden * i;
    positions.set(node.id, { x: r * Math.cos(theta), y, z: r * Math.sin(theta) });
  });
  const adjacency = new Map();
  nodes.forEach((node) => adjacency.set(node.id, []));
  edges.forEach((edge) => {
    if (adjacency.has(edge.source)) adjacency.get(edge.source).push(edge.target);
    if (adjacency.has(edge.target)) adjacency.get(edge.target).push(edge.source);
  });
  const iterations = 220;
  const repulsion = 1.1, springLength = 2.0, springK = 0.08, centerK = 0.02, maxStep = 0.5;
  for (let iter = 0; iter < iterations; iter++) {
    const disp = new Map();
    nodes.forEach((node) => disp.set(node.id, { x: 0, y: 0, z: 0 }));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = nodes[i], b = nodes[j];
        const pa = positions.get(a.id), pb = positions.get(b.id);
        let dx = pa.x - pb.x, dy = pa.y - pb.y, dz = pa.z - pb.z;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 1e-6) { d2 = 1e-6; dx = (i % 2 ? 1 : -1) * 1e-3; dy = 0; dz = 0; }
        const d = Math.sqrt(d2);
        const force = repulsion / d2;
        const fx = (dx / d) * force, fy = (dy / d) * force, fz = (dz / d) * force;
        disp.get(a.id).x += fx; disp.get(a.id).y += fy; disp.get(a.id).z += fz;
        disp.get(b.id).x -= fx; disp.get(b.id).y -= fy; disp.get(b.id).z -= fz;
      }
    }
    edges.forEach((edge) => {
      const pa = positions.get(edge.source), pb = positions.get(edge.target);
      if (!pa || !pb) return;
      let dx = pb.x - pa.x, dy = pb.y - pa.y, dz = pb.z - pa.z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-6;
      const force = springK * (d - springLength);
      const fx = (dx / d) * force, fy = (dy / d) * force, fz = (dz / d) * force;
      disp.get(edge.source).x += fx; disp.get(edge.source).y += fy; disp.get(edge.source).z += fz;
      disp.get(edge.target).x -= fx; disp.get(edge.target).y -= fy; disp.get(edge.target).z -= fz;
    });
    nodes.forEach((node) => {
      const p = positions.get(node.id), d = disp.get(node.id);
      d.x += -p.x * centerK; d.y += -p.y * centerK; d.z += -p.z * centerK;
      const len = Math.sqrt(d.x * d.x + d.y * d.y + d.z * d.z);
      if (len > maxStep) { d.x = (d.x / len) * maxStep; d.y = (d.y / len) * maxStep; d.z = (d.z / len) * maxStep; }
      p.x += d.x; p.y += d.y; p.z += d.z;
    });
  }
  return positions;
}

function buildGraph(data) {
  state.nodes = data.nodes || [];
  state.edges = data.relationships || [];
  state.positions = computeLayout(state.nodes, state.edges);
  while (graphGroup.children.length) graphGroup.remove(graphGroup.children[0]);
  clearSelection();
  const meshes = new Map();
  for (const node of state.nodes) {
    const mesh = makeNodeMesh(node);
    mesh.position.copy(vec(state.positions.get(node.id)));
    const label = makeLabel(shortName(node));
    label.position.y = 0.8;
    mesh.add(label);
    graphGroup.add(mesh);
    meshes.set(node.id, mesh);
  }
  for (const edge of state.edges) {
    if (!meshes.has(edge.source) || !meshes.has(edge.target)) continue;
    const a = state.positions.get(edge.source);
    const b = state.positions.get(edge.target);
    const geometry = new THREE.BufferGeometry().setFromPoints([vec(a), vec(b)]);
    const material = new THREE.LineBasicMaterial({
      color: EDGE_COLOR[edge.type] ?? DEFAULT_EDGE_COLOR,
      transparent: true,
      opacity: 0.75,
    });
    const line = new THREE.Line(geometry, material);
    line.userData.kind = 'edge';
    line.userData.edge = edge;
    graphGroup.add(line);
  }
  focusCamera();
}

function focusCamera() {
  let maxR = 1;
  for (const p of state.positions.values()) {
    const r = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
    if (r > maxR) maxR = r;
  }
  const dist = Math.max(6, maxR * 2.6);
  controls.target.set(0, 0, 0);
  camera.position.set(0, 0, dist);
  controls.update();
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function setStatus(message) { document.getElementById('status').textContent = message; }

function clearDetails() {
  document.getElementById('details').innerHTML = '<p class="hint">Select a node or an edge.</p>';
}

function showNodeDetails(node) {
  const rows = [];
  rows.push(['Name', node.id]);
  rows.push(['Type', node.type]);
  if (node.type === 'database_table' || node.type === 'configuration') rows.push(['Name', node.name]);
  if (node.module_name) rows.push(['Module', node.module_name]);
  if (node.file) rows.push(['File', node.file]);
  if (node.line != null) rows.push(['Line', node.line]);
  if (node.is_test) rows.push(['Test', 'yes']);
  if (node.type === 'configuration' && node.sensitive) rows.push(['Sensitive', 'yes']);
  if (node.type === 'external_or_unresolved') rows.push(['Status', 'external / unresolved']);
  const define = state.edges.find((e) => e.target === node.id && e.type === 'defines_table');
  if (define) rows.push(['Defined by', define.source]);
  let html = '<dl class="details">';
  for (const [k, v] of rows) html += `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;
  html += '</dl>';
  const incident = state.edges.filter((e) => e.source === node.id || e.target === node.id);
  if (incident.length) {
    html += '<h3>Relationships</h3><ul class="rel-list">';
    for (const e of incident) {
      const other = e.source === node.id ? e.target : e.source;
      html += `<li><span class="badge">${esc(e.type)}</span>${esc(other)}</li>`;
    }
    html += '</ul>';
  }
  document.getElementById('details').innerHTML = html;
}

function showEdgeDetails(edge) {
  const rows = [];
  rows.push(['Type', edge.type]);
  rows.push(['Source', edge.source]);
  rows.push(['Target', edge.target]);
  if (edge.source_file) rows.push(['Source file', edge.source_file]);
  if (edge.source_line != null) rows.push(['Source line', edge.source_line]);
  if (edge.resolution_status) rows.push(['Resolution', edge.resolution_status]);
  if (edge.confidence) rows.push(['Confidence', edge.confidence]);
  if (edge.details && Object.keys(edge.details).length) rows.push(['Details', JSON.stringify(edge.details)]);
  let html = '<dl class="details">';
  for (const [k, v] of rows) html += `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;
  html += '</dl>';
  document.getElementById('details').innerHTML = html;
}

function highlight(obj) {
  if (obj.userData.kind === 'node') {
    obj.userData._scale = obj.scale.x;
    obj.scale.setScalar(obj.scale.x * 1.35);
  } else if (obj.material && obj.material.color) {
    obj.userData._color = obj.material.color.getHex();
    obj.material.color.setHex(0xffffff);
  }
}

function unhighlight(obj) {
  if (obj.userData.kind === 'node') {
    if (obj.userData._scale != null) obj.scale.setScalar(obj.userData._scale);
  } else if (obj.material && obj.material.color && obj.userData._color != null) {
    obj.material.color.setHex(obj.userData._color);
  }
}

function clearSelection() {
  if (state.selected) { unhighlight(state.selected); state.selected = null; }
  clearDetails();
}

function findSelectable(object) {
  let current = object;
  while (current) {
    if (current.userData && (current.userData.kind === 'node' || current.userData.kind === 'edge')) return current;
    current = current.parent;
  }
  return null;
}

function selectObject(obj) {
  if (state.selected) unhighlight(state.selected);
  state.selected = obj;
  highlight(obj);
  if (obj.userData.kind === 'node') showNodeDetails(obj.userData.node);
  else showEdgeDetails(obj.userData.edge);
}

container.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(graphGroup.children, true);
  if (!hits.length) { clearSelection(); return; }
  const obj = findSelectable(hits[0].object);
  if (obj) selectObject(obj);
  else clearSelection();
});
async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
  return data;
}

async function loadGraph(symbol) {
  if (!symbol) return;
  const depth = Number(document.getElementById('depth').value) || 1;
  setStatus(`Loading ${symbol} (depth ${depth})…`);
  try {
    const params = new URLSearchParams();
    params.set('symbol', symbol);
    params.set('depth', String(depth));
    if (state.repository) params.set('root', state.repository);
    const data = await getJson(`/api/graph?${params.toString()}`);
    state.repository = data.repository;
    document.getElementById('repo').textContent = `Repository: ${data.repository}`;
    buildGraph(data);
    setStatus(`${data.nodes.length} nodes · ${data.relationships.length} relationships`);
  } catch (err) {
    setStatus('Error: ' + err.message);
  }
}

async function refreshSuggestions(query) {
  try {
    const params = new URLSearchParams();
    params.set('q', query || '');
    if (state.repository) params.set('root', state.repository);
    const data = await getJson(`/api/search?${params.toString()}`);
    const list = document.getElementById('symbols');
    list.innerHTML = '';
    for (const symbol of data.symbols || []) {
      const option = document.createElement('option');
      option.value = symbol.qualified_name;
      list.appendChild(option);
    }
  } catch (err) { /* suggestions are best-effort */ }
}

async function init() {
  try {
    const data = await getJson('/api/repositories');
    state.repository = (data.repositories && data.repositories[0]) || null;
    document.getElementById('repo').textContent = state.repository
      ? `Repository: ${state.repository}`
      : 'Repository: (none)';
    await refreshSuggestions('');
    await loadGraph(document.getElementById('symbol').value || 'models.User');
  } catch (err) {
    setStatus('Error: ' + err.message);
  }
}

document.getElementById('load').addEventListener('click', () => loadGraph(document.getElementById('symbol').value));
document.getElementById('symbol').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadGraph(e.target.value); });
document.getElementById('symbol').addEventListener('input', (e) => refreshSuggestions(e.target.value));

init();



