// Pairs — Liquidity Universe. Real-time WebGL view over the live pairing database.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { createSpace } from './space-audio.js';

// ---------------------------------------------------------------- env
const QS = new URLSearchParams(location.search);
const CAP = QS.has('cap');
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches && !CAP;
const FINE = matchMedia('(pointer: fine)').matches;
const isMobile = () => innerWidth <= 760;
const SOL = 'So11111111111111111111111111111111111111112';
const STABLES = new Set(['USDC', 'USDT', 'USD1', 'PYUSD', 'USDS']);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const seg = (t, a, b) => clamp((t - a) / (b - a), 0, 1);
const ease = { io: t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2, out: t => 1 - Math.pow(1 - t, 3), outExpo: t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t) };
const fr = x => x - Math.floor(x);
if (FINE) document.body.classList.add('fine');

function usd(n) {
  if (n == null || !isFinite(n)) return '—'; const a = Math.abs(n);
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B'; if (a >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return '$' + (n / 1e3).toFixed(1) + 'K'; if (a >= 1e3) return '$' + (n / 1e3).toFixed(2) + 'K';
  if (a >= 1) return '$' + n.toFixed(0); return '$' + n.toFixed(2);
}
function priceHTML(p) {
  if (p == null || !isFinite(p) || p <= 0) return '—';
  if (p >= 1) return '$' + p.toLocaleString('en-US', { maximumFractionDigits: p >= 1000 ? 0 : 4 });
  const s = p.toFixed(20); const m = s.match(/^0\.(0*)(\d+)/); const z = m[1].length; const sig = m[2].slice(0, 4);
  return z >= 3 ? `$0.0<sub>${z}</sub>${sig}` : '$' + p.toPrecision(4);
}
function ageOf(iso) {
  if (!iso) return '—'; const s = (Date.now() - Date.parse(iso)) / 1000; if (!isFinite(s)) return '—';
  if (s < 45) return 'just now'; if (s < 3600) return Math.round(s / 60) + 'm'; if (s < 86400) return Math.round(s / 3600) + 'h';
  const d = s / 86400; return d < 365 ? Math.round(d) + 'd' : (d / 365).toFixed(1) + 'y';
}
const short = (m, a = 4, b = 4) => m ? m.slice(0, a) + '…' + m.slice(-b) : '';
const DEXL = { 'orca': 'Orca', 'meteora': 'Meteora DLMM', 'meteora-damm-v2': 'Meteora DAMM', 'meteora-dbc': 'Meteora DBC', 'raydium-concentrated': 'Raydium CLMM', 'raydium-clmm': 'Raydium CLMM', 'raydium-standard': 'Raydium AMM', 'raydium': 'Raydium', 'raydium-launchlab': 'LaunchLab', 'pump-fun': 'Pump.fun', 'pumpswap': 'PumpSwap', 'letsbonk-fun': 'LetsBonk', 'zerofi': 'ZeroFi', 'fluxbeam': 'FluxBeam', 'saros-amm': 'Saros', 'dexlab': 'Dexlab', 'stonkfun': 'Stonk.fun', 'bags-fm': 'Bags', 'moonshot': 'Moonshot', 'manifest': 'Manifest' };
const dexName = d => DEXL[d] || (d ? d.replace(/-/g, ' ') : '—');
const dexCol = d => !d ? '#E9E4DA' : d.startsWith('raydium') ? '#8FD3FF' : d === 'orca' ? '#FFC562' : d.startsWith('meteora') ? '#FF7A52' : (d === 'pump-fun' || d === 'pumpswap') ? '#8CFFC1' : d === 'letsbonk-fun' ? '#FF9F40' : '#E9E4DA';
const symOf = t => !t ? '?' : (t.mint === SOL ? 'SOL' : (t.sym || short(t.mint, 4, 3)));
// token logos: server-side proxy (/api/v1/img) over a generated avatar, so a missing logo never shows a broken image
const PAL = ['255,183,94', '143,211,255', '140,255,193', '255,122,82', '255,197,98', '233,228,218'];
function hashOf(m) { let h = 2166136261; for (const c of m || '') { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; } return h; }
function identStyle(m) { const h = hashOf(m), a = PAL[h % PAL.length], b = PAL[(h >>> 7) % PAL.length], g = (h >>> 14) % 360; return `background:radial-gradient(circle at 32% 28%,rgba(${a},.95),rgba(${b},.6) 52%,rgba(12,12,14,.95) 100%),conic-gradient(from ${g}deg,rgb(${a}),rgb(${b}),rgb(${a}))`; }
function initials(sym, m) { const t = (sym || '').replace(/^\$/, '').replace(/[^\p{L}\p{N}]/gu, ''); return esc((t ? [...t].slice(0, 2).join('') : (m || '?').slice(0, 2)).toUpperCase()); }
function logo(m, sym, size = 24, cls = '') {
  const px = size <= 16 ? 32 : size <= 32 ? 64 : 128;
  return `<span class="tl ${cls}" style="--s:${size}px;${identStyle(m || '?')}"><b>${initials(sym, m)}</b>${m ? `<img alt="" loading="lazy" decoding="async" src="/api/v1/img/${encodeURIComponent(m)}?s=${px}">` : ''}</span>`;
}
document.addEventListener('load', e => { const t = e.target; if (t.tagName === 'IMG' && t.parentNode?.classList?.contains('tl')) t.parentNode.classList.add('ok'); }, true);
document.addEventListener('error', e => {
  const t = e.target; if (t.tagName !== 'IMG' || !t.parentNode?.classList?.contains('tl')) return;
  // one quiet retry (the server may still be fetching the logo), then keep the generated avatar
  if (!t.dataset.r) { t.dataset.r = 1; const src = t.getAttribute('src'); t.removeAttribute('src'); setTimeout(() => { if (t.isConnected) t.src = src + '&r=1'; }, 3500); }
  else t.remove();
}, true);
const api = async (u, opt) => { const r = await fetch(u, opt); if (!r.ok) throw new Error(r.status + ' ' + u); return r.json(); };

// ---------------------------------------------------------------- clock (real time, or virtual for frame-exact capture)
const frameFns = []; const onFrame = f => frameFns.push(f);
let NOW = 0, lastT = 0;
const born = new WeakMap();
function syncAnims(t) { for (const a of document.getAnimations()) { if (!born.has(a)) born.set(a, t); a.pause(); a.currentTime = Math.max(0, (t - born.get(a)) * 1000); } }
function step(t) { const dt = clamp(t - lastT, 0, .1); lastT = t; NOW = t; if (CAP) syncAnims(t); for (const f of frameFns) f(t, dt); }

// ---------------------------------------------------------------- GPU tier
function glSupport() { try { const c = document.createElement('canvas'); const g = c.getContext('webgl2') || c.getContext('webgl'); if (!g) return null; const ext = g.getExtension('WEBGL_debug_renderer_info'); return { r: ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '' }; } catch (e) { return null; } }
const GL = QS.get('gl') === '0' ? null : glSupport();
let tier = QS.get('lod') || (isMobile() || !FINE ? 'mid' : 'high');
if (!QS.get('lod') && GL && /swiftshader|llvmpipe|software|mali-4|adreno \(tm\) [34]/i.test(GL.r)) tier = 'low';
if (!QS.get('lod') && navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4 && tier === 'high') tier = 'mid';
const NPTS = { low: 30000, mid: 60000, high: 130000 }[tier] || 60000;
let DPR = Math.min(devicePixelRatio || 1, tier === 'high' ? 2 : tier === 'mid' ? 1.5 : 1);
if (CAP) DPR = Math.min(devicePixelRatio || 1, 2);

// ---------------------------------------------------------------- state
const S = {
  introEnd: RM ? 0 : 5.6, ready: false, t0Intro: 0,
  focus: { from: 0, to: 0, t0: -99, dur: 2.6 }, bodiesT0: -99, starT0: -99, bodiesOut: { t0: -99 },
  tok: null, tokMint: null, pairs: [], sort: { k: 'liq', asc: false }, drawer: null,
  yaw: 0, yawV: 0, dist: 1, distT: 1, drag: null, mouse: { x: .5, y: .5 },
  stats: null, novaQ: [], nova: null, nextNovaT: 0, feed: [], cursor: 0, sound: false,
};
const focusVal = t => { const f = S.focus; const k = seg(t, f.t0, f.t0 + f.dur); return f.from + (f.to - f.from) * k; };

// ---------------------------------------------------------------- load
const ld = $('loader'), ldBar = $('ldBar');
async function loadGalaxy() {
  const res = await fetch(`/api/v1/galaxy.bin?n=${NPTS}`);
  if (!res.ok) throw new Error('galaxy ' + res.status);
  const total = +res.headers.get('content-length') || NPTS * 24; const rd = res.body.getReader(); const buf = new Uint8Array(total); let got = 0;
  for (; ;) { const { done, value } = await rd.read(); if (done) break; if (got + value.length > buf.length) break; buf.set(value, got); got += value.length; ldBar.style.width = (100 * got / total).toFixed(0) + '%'; }
  return new Float32Array(buf.buffer, 0, Math.floor(got / 24) * 6);
}
const statsP = api('/api/v1/stats').catch(() => null);
const newP = api('/api/v1/new?limit=100').catch(() => ({ pools: [], cursor: 0 }));
let P;
try { P = await loadGalaxy(); } catch (e) { P = new Float32Array(0); }
S.stats = await statsP;
const N = P.length / 6;

// ---------------------------------------------------------------- galaxy layout (shared by stored + newborn pools)
const ARMS = 5;
const DEXC = [[.56, .83, 1], [.56, .83, 1], [1, .77, .38], [1, .48, .32], [1, .48, .32], [1, .48, .32], [.55, 1, .76], [.55, 1, .76], [.56, .83, 1], [1, .62, .25], [.91, .89, .85]];
function layout(L, d, ht, hp, out, i) {
  const arm = Math.floor(ht * ARMS);
  const rt = 3 + 78 * Math.pow(fr(ht * 7.13), .75);
  const th = arm * Math.PI * 2 / ARMS + rt * 0.085 + (fr(ht * 13.7) - .5) * .55;
  const jr = (0.3 + 2.4 * Math.pow(fr(hp * 913.71), 1.5)) * (0.5 + rt / 60), ja = fr(hp * 271.37) * Math.PI * 2;
  const thick = 0.6 + 4.5 * Math.exp(-rt / 14);
  out.g[i * 3] = Math.cos(th) * rt + Math.cos(ja) * jr; out.g[i * 3 + 1] = (fr(hp * 1777.3) - .5) * 2 * thick * fr(hp * 557.7); out.g[i * 3 + 2] = Math.sin(th) * rt + Math.sin(ja) * jr;
  const u = fr(hp * 91.7) * 2 - 1, ph = fr(hp * 37.1) * Math.PI * 2, R = 150 + 90 * fr(hp * 11.9), s = Math.sqrt(1 - u * u);
  out.f[i * 3] = Math.cos(ph) * s * R; out.f[i * 3 + 1] = u * R * .55; out.f[i * 3 + 2] = Math.sin(ph) * s * R;
  const c = DEXC[d] || DEXC[10], k = 0.35 + 0.65 * clamp((L - 2.1) / 3.4, 0, 1), wc = Math.exp(-rt / 16) * .85, warm = [1, .74, .45];
  for (let j = 0; j < 3; j++) out.c[i * 3 + j] = (c[j] * (1 - wc) + warm[j] * wc) * k;
  out.s[i] = 0.55 + Math.pow(Math.max(0, L - 2.1), 1.6) * 0.55; out.r[i] = hp; out.t[i] = ht; out.b[i] = -1;
}
const SPARE = 4000, CAPN = N + SPARE;
const G = { g: new Float32Array(CAPN * 3), f: new Float32Array(CAPN * 3), c: new Float32Array(CAPN * 3), s: new Float32Array(CAPN), r: new Float32Array(CAPN), t: new Float32Array(CAPN), b: new Float32Array(CAPN) };
for (let i = 0; i < N; i++) layout(P[i * 6], P[i * 6 + 2] | 0, P[i * 6 + 4], P[i * 6 + 5], G, i);
let liveN = N;

// ---------------------------------------------------------------- UI chrome (works with or without WebGL)
const nf = n => n == null ? '—' : Math.round(n).toLocaleString('en-US');
function paintStats() { const s = S.stats; if (!s) return; $('cnt').textContent = nf(s.pools); $('rate').textContent = s.new_per_min ?? '—'; }
paintStats();
$('kicker').textContent = `${nf(N)} live pools with liquidity · each point is one pool`;
const title = $('title');
title.querySelectorAll('.ln').forEach(ln => { const nodes = [...ln.childNodes]; ln.innerHTML = ''; nodes.forEach(n => { const host = n.nodeType === 3 ? ln : ln.appendChild(n.cloneNode(false)); [...n.textContent].forEach(ch => { const s = document.createElement('span'); s.className = 'ch'; s.textContent = ch === ' ' ? '\u00a0' : ch; host.appendChild(s); }); }); });
const chars = [...title.querySelectorAll('.ch')];
const op = (el, v) => { el.style.opacity = v; };
function toast(msg) { const t = $('toast'); t.textContent = msg; t.style.opacity = 1; clearTimeout(toast._t); toast._t = setTimeout(() => t.style.opacity = 0, 1800); }

// ---------------------------------------------------------------- WebGL scene
let renderer = null, composer, scene, camera, gm, bm, galaxy, star, novaMesh, fin, bloom, rings = [], bodyGeo;
const MAXB = 220;
const bodies = [];
if (GL && N) {
  try {
    const canvas = $('gl');
    renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: CAP });
  } catch (e) { renderer = null; }
}
if (!renderer) document.body.classList.add('nogl');

if (renderer) {
  renderer.setPixelRatio(DPR); renderer.setSize(innerWidth, innerHeight); renderer.setClearColor(0x030304, 1);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(52, innerWidth / innerHeight, 0.05, 2000);
  const gg = new THREE.BufferGeometry();
  const A = (k, n) => { const a = new THREE.BufferAttribute(G[k], n); a.setUsage(THREE.DynamicDrawUsage); return a; };
  gg.setAttribute('position', A('g', 3)); gg.setAttribute('aF', A('f', 3)); gg.setAttribute('aC', A('c', 3)); gg.setAttribute('aS', A('s', 1)); gg.setAttribute('aR', A('r', 1)); gg.setAttribute('aT', A('t', 1)); gg.setAttribute('aB', A('b', 1));
  gg.setDrawRange(0, liveN);
  gm = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uT: { value: 0 }, uRT: { value: 0 }, uReveal: { value: 0 }, uFocus: { value: 0 }, uPx: { value: innerHeight * DPR }, uNova: { value: new THREE.Vector4(0, 0, 0, -10) }, uTok: { value: -1 }, uDen: { value: clamp(55000 / Math.max(1, N), .25, 1) } },
    vertexShader: `
    attribute vec3 aF; attribute vec3 aC; attribute float aS; attribute float aR; attribute float aT; attribute float aB;
    uniform float uT,uRT,uReveal,uFocus,uPx,uTok,uDen; uniform vec4 uNova;
    varying vec3 vC; varying float vA;
    mat2 rot(float a){float c=cos(a),s=sin(a);return mat2(c,-s,s,c);}
    void main(){
      vec3 p = position; float r = length(p.xz);
      p.xz = rot(uRT*(0.9/(r+6.)))*p.xz;
      float f = clamp((uFocus*1.3 - aR*0.3),0.,1.);
      float imp = smoothstep(0.,.55,f), burst = smoothstep(.5,1.,f);
      float mine = step(abs(aT-uTok),1e-6);
      vec3 q = mix(aF, vec3(0.), mine); q.xz = rot(uT*0.01)*q.xz;
      vec3 p1 = p*mix(1., 0.03+0.05*aR, imp*imp);
      p1.xz = rot(imp*imp*4.0*(1.2-r/120.))*p1.xz;
      vec3 mid = mix(p1, q, burst*burst*(3.-2.*burst));
      float rev = smoothstep(r-6., r, uReveal);
      float young = aB>0. ? exp(-max(uT-aB,0.)*0.08) : 0.;
      vec4 mv = modelViewMatrix*vec4(mid,1.);
      gl_Position = projectionMatrix*mv;
      float tw = 0.75+0.25*sin(uT*2.3+aR*80.);
      float nova = uNova.w>0. ? exp(-pow(length(mid-uNova.xyz)-uNova.w*14.,2.)*0.5)*(1.-uNova.w)*0.35 : 0.;
      float ps = aS*uPx*0.07/(-mv.z)*(1.+nova*2.5+young*1.5); gl_PointSize = clamp(ps, 1.2, 48.);
      vC = (aC + vec3(1.,.75,.45)*young)*(tw + nova*3.)*(1.+imp*(1.-burst)*0.6*uDen);
      vA = rev*mix(1.,0.7,burst)*mix(1.,0.13*uDen,imp*(1.-burst))*mix(1.,0.,mine*burst)*smoothstep(0.08,1.2,-mv.z)*clamp(ps/1.2,0.35,1.)/(1.+ps*0.04);
    }`,
    fragmentShader: `varying vec3 vC; varying float vA;
    void main(){ vec2 d=gl_PointCoord-.5; float r=length(d); if(r>.5) discard; float a=exp(-r*r*22.)+0.25*exp(-r*r*5.); gl_FragColor=vec4(vC*a*vA,1.); }`
  });
  galaxy = new THREE.Points(gg, gm); galaxy.rotation.y = 1.3; galaxy.frustumCulled = false; scene.add(galaxy);

  // dust lanes
  const DN = tier === 'low' ? 8000 : 22000, dpos = new Float32Array(DN * 3), dr = new Float32Array(DN);
  for (let i = 0; i < DN; i++) { const r = Math.pow(Math.random(), .6) * 95, arm = Math.floor(Math.random() * ARMS); const th = arm * Math.PI * 2 / ARMS + r * 0.085 + (Math.random() - .5) * .9; dpos.set([Math.cos(th) * r + (Math.random() - .5) * 4, (Math.random() - .5) * (1 + 4 * Math.exp(-r / 14)), Math.sin(th) * r + (Math.random() - .5) * 4], i * 3); dr[i] = Math.random(); }
  const dg = new THREE.BufferGeometry(); dg.setAttribute('position', new THREE.BufferAttribute(dpos, 3)); dg.setAttribute('aR', new THREE.BufferAttribute(dr, 1));
  const dm = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uRT: gm.uniforms.uRT, uReveal: gm.uniforms.uReveal, uFocus: gm.uniforms.uFocus, uPx: gm.uniforms.uPx },
    vertexShader: `attribute float aR; uniform float uRT,uReveal,uFocus,uPx; varying float vA;
    mat2 rot(float a){float c=cos(a),s=sin(a);return mat2(c,-s,s,c);}
    void main(){ vec3 p=position; float r=length(p.xz); p.xz=rot(uRT*(0.9/(r+6.)))*p.xz; p*= 1.0+uFocus*(1.6+aR*2.); vec4 mv=modelViewMatrix*vec4(p,1.); gl_Position=projectionMatrix*mv;
     gl_PointSize=clamp(uPx*0.03/(-mv.z),1.,8.); vA=smoothstep(r-8.,r,uReveal)*(1.-uFocus*.8)*0.5*smoothstep(0.1,2.,-mv.z);}`,
    fragmentShader: `varying float vA; void main(){ float r=length(gl_PointCoord-.5); if(r>.5) discard; gl_FragColor=vec4(vec3(.85,.7,.55)*vA*(1.-r*2.),1.);}`
  });
  const dust = new THREE.Points(dg, dm); dust.rotation.y = 1.3; dust.frustumCulled = false; scene.add(dust);

  // token system: orbiting pairs
  bodyGeo = new THREE.BufferGeometry();
  for (const [k, n] of [['position', 3], ['aO', 4], ['aC', 3], ['aS', 1], ['aD', 1]]) { const a = new THREE.BufferAttribute(new Float32Array(MAXB * n), n); a.setUsage(THREE.DynamicDrawUsage); bodyGeo.setAttribute(k, a); }
  bodyGeo.setDrawRange(0, 0);
  const ORB = `vec3 orbit(vec4 o, float t){ float w = 3.2/pow(o.x,1.5); float a=o.y + t*w; vec3 p=vec3(cos(a)*o.x,0.,sin(a)*o.x); float c=cos(o.z),s=sin(o.z); return vec3(p.x, p.y*c - p.z*s, p.y*s + p.z*c); }`;
  bm = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uT: gm.uniforms.uT, uFocus: { value: 0 }, uOut: { value: 0 }, uPx: gm.uniforms.uPx },
    vertexShader: `attribute vec4 aO; attribute vec3 aC; attribute float aS; attribute float aD; uniform float uT,uFocus,uOut,uPx; varying vec3 vC; varying float vA; ${ORB}
    void main(){ float k=clamp((uFocus-aD*0.45)/0.55,0.,1.); float e=1.-pow(1.-k,3.); vec3 p=mix(position, orbit(aO,uT), e);
     vec4 mv=modelViewMatrix*vec4(p,1.); gl_Position=projectionMatrix*mv; gl_PointSize=clamp(aS*uPx*0.14/(-mv.z),2.,64.)*(0.4+0.6*e); vC=aC; vA=k*(1.-uOut); }`,
    fragmentShader: `varying vec3 vC; varying float vA; void main(){ vec2 d=gl_PointCoord-.5; float r=length(d); if(r>.5) discard; float core=smoothstep(.22,.16,r); float halo=exp(-r*r*14.)*.7;
    gl_FragColor=vec4((vC*halo + vec3(1.)*core*.9)*vA,1.);}`
  });
  const bodyPts = new THREE.Points(bodyGeo, bm); bodyPts.frustumCulled = false; scene.add(bodyPts);
  const ringMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uA: { value: 0 }, uC: { value: new THREE.Color() } },
    vertexShader: `varying float vU; attribute float aU; void main(){ vU=aU; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
    fragmentShader: `uniform float uA; uniform vec3 uC; varying float vU; void main(){ float draw=step(vU,uA); gl_FragColor=vec4(uC*0.22*draw,1.);}`
  });
  [[9.8, .22, [.75, 1, .55]], [16.1, -.14, [.62, .85, 1]], [24, .05, [1, .78, .45]], [34, -.08, [1, .78, .45]]].forEach(([R, inc, c]) => {
    const pts = [], us = []; for (let i = 0; i <= 256; i++) { const a = i / 256 * Math.PI * 2; const v = new THREE.Vector3(Math.cos(a) * R, 0, Math.sin(a) * R); v.applyAxisAngle(new THREE.Vector3(1, 0, 0), inc); pts.push(v); us.push(i / 256); }
    const g = new THREE.BufferGeometry().setFromPoints(pts); g.setAttribute('aU', new THREE.BufferAttribute(new Float32Array(us), 1));
    const m = ringMat.clone(); m.uniforms.uC.value = new THREE.Color(...c); const l = new THREE.Line(g, m); l.frustumCulled = false; scene.add(l); rings.push(l);
  });
  star = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uT: gm.uniforms.uT, uI: { value: 0 } },
    vertexShader: `varying vec2 vU; void main(){ vU=uv-.5; vec4 mv=modelViewMatrix*vec4(0,0,0,1); mv.xy+=position.xy*18.; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform float uT,uI; varying vec2 vU; void main(){ float r=length(vU)*2.; float a=atan(vU.y,vU.x);
     float core=smoothstep(.09,.0,r); float glow=exp(-r*9.)*1.4; float rays=exp(-abs(vU.y)*160.)*exp(-abs(vU.x)*5.)*.55 + pow(abs(sin(a*3.+uT*.15)),30.)*exp(-r*6.)*.25;
     vec3 c=vec3(1.,.72,.36)*glow + vec3(1.,.9,.75)*core*3. + vec3(1.,.8,.6)*rays; gl_FragColor=vec4(c*uI,1.);}`
  }));
  star.frustumCulled = false; scene.add(star);
  novaMesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uK: { value: -1 }, uS: { value: 6 } },
    vertexShader: `uniform float uS; varying vec2 vU; void main(){ vU=uv-.5; vec4 mv=modelViewMatrix*vec4(0,0,0,1); mv.xy+=position.xy*uS; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform float uK; varying vec2 vU; void main(){ if(uK<0.||uK>1.) discard; float r=length(vU)*2.; float ring=exp(-pow((r-uK*.9)*14.,2.))*(1.-uK);
     float flash=exp(-r*r*400.)*pow(1.-uK,8.)*3.; float cross=exp(-abs(vU.y)*260.)*exp(-abs(vU.x)*4.)*pow(1.-uK,2.)*1.2;
     gl_FragColor=vec4(vec3(1.,.82,.55)*(ring*1.6+flash+cross),1.);}`
  }));
  novaMesh.frustumCulled = false; scene.add(novaMesh);

  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.9, 0.55, 0.05); composer.addPass(bloom);
  fin = new ShaderPass({
    uniforms: { tDiffuse: { value: null }, uT: { value: 0 }, uLens: { value: new THREE.Vector3(.5, .5, 0) }, uAsp: { value: innerWidth / innerHeight }, uFade: { value: 0 }, uRes: { value: new THREE.Vector2(innerWidth, innerHeight) } },
    vertexShader: `varying vec2 vUv; void main(){vUv=uv; gl_Position=vec4(position.xy,0.,1.);}`,
    fragmentShader: `uniform sampler2D tDiffuse; uniform float uT,uAsp,uFade; uniform vec3 uLens; uniform vec2 uRes; varying vec2 vUv;
    float h(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
    void main(){ vec2 uv=vUv; vec2 d=uv-uLens.xy; d.x*=uAsp; float r=length(d);
      float Re=0.055*uLens.z*min(1.,uAsp*0.9); uv -= (d/max(r,1e-4))*(Re*Re/max(r,Re))*smoothstep(Re*0.7,Re*1.7,r)*vec2(1./uAsp,1.)*0.9;
      vec2 c=uv-.5; float ca=0.0028*dot(c,c)*4.;
      vec3 col=vec3(texture2D(tDiffuse,uv+c*ca).r, texture2D(tDiffuse,uv).g, texture2D(tDiffuse,uv-c*ca).b);
      col += vec3(1.,.8,.55)*exp(-pow((r-Re*1.05)*90.,2.))*0.18*uLens.z;
      col = col/(1.+col*0.18); col = pow(col,vec3(0.95));
      col *= 1.-0.55*pow(length(c)*1.25,2.6);
      col += (h(vUv*uRes+fract(uT*7.3))-.5)*0.045;
      gl_FragColor=vec4(col*uFade,1.);}`
  });
  composer.addPass(fin);
  renderer.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); document.body.classList.add('nogl'); drawFallback(); });
}
function onResize() {
  if (renderer) {
    renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
    gm.uniforms.uPx.value = innerHeight * renderer.getPixelRatio(); fin.uniforms.uAsp.value = innerWidth / innerHeight; fin.uniforms.uRes.value.set(innerWidth, innerHeight);
  }
  if (document.body.classList.contains('nogl')) drawFallback();
}
addEventListener('resize', onResize);

// ---------------------------------------------------------------- 2D fallback (no WebGL / context lost)
function drawFallback() {
  const c = $('fallback'), d = Math.min(devicePixelRatio || 1, 2); c.width = innerWidth * d; c.height = innerHeight * d; const x = c.getContext('2d');
  x.fillStyle = '#030304'; x.fillRect(0, 0, c.width, c.height); x.globalCompositeOperation = 'lighter';
  const sc = Math.min(c.width, c.height) / 190, cx = c.width / 2, cy = c.height * .52, ca = Math.cos(1.3), sa = Math.sin(1.3);
  const n = Math.min(N, 40000);
  for (let i = 0; i < n; i++) {
    const gx = G.g[i * 3], gz = G.g[i * 3 + 2], gy = G.g[i * 3 + 1]; const rx = gx * ca + gz * sa, rz = -gx * sa + gz * ca;
    const px = cx + rx * sc, py = cy + (rz * .42 - gy) * sc; const s = Math.min(3 * d, Math.max(.7, G.s[i] * d * .45));
    x.fillStyle = `rgba(${G.c[i * 3] * 255 | 0},${G.c[i * 3 + 1] * 255 | 0},${G.c[i * 3 + 2] * 255 | 0},.42)`; x.fillRect(px - s / 2, py - s / 2, s, s);
  }
  const gr = x.createRadialGradient(cx, cy, 0, cx, cy, 60 * d); gr.addColorStop(0, 'rgba(255,190,120,.5)'); gr.addColorStop(1, 'rgba(255,190,120,0)'); x.fillStyle = gr; x.fillRect(0, 0, c.width, c.height);
}
if (!renderer) drawFallback();

// ---------------------------------------------------------------- camera
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const camPath = new THREE.CatmullRomCurve3([V(2, 0.6, 6), V(10, 1.4, 22), V(22, 6, 46), V(18, 30, 88), V(0, 58, 112)]);
const lookPath = new THREE.CatmullRomCurve3([V(6, 0.4, 14), V(14, 1, 34), V(8, 2, 20), V(0, 0, 0), V(0, 0, 0)]);
const proj = new THREE.Vector3();
function toScreen(v) { proj.copy(v).project(camera); return [(proj.x * .5 + .5) * innerWidth, (-proj.y * .5 + .5) * innerHeight, proj.z]; }
function orbitPos(b, t) { const w = 3.2 / Math.pow(b.R, 1.5), a = b.a0 + t * w; const v = V(Math.cos(a) * b.R, 0, Math.sin(a) * b.R); return v.applyAxisAngle(V(1, 0, 0), b.inc); }
const tmpV = new THREE.Vector3(), tmpV2 = new THREE.Vector3(), Y = V(0, 1, 0);
function baseCam(t, out, look) {
  if (t < S.introEnd) { const k = ease.io(seg(t, 0, S.introEnd)); out.copy(camPath.getPointAt(k)); look.copy(lookPath.getPointAt(k)); }
  else { out.set(0, 58, 112); look.set(0, 0, 0); if (!RM) out.applyAxisAngle(Y, (t - S.introEnd) * 0.018); }
  out.applyAxisAngle(Y, S.yaw); out.multiplyScalar(t < S.introEnd ? 1 : S.dist);
}

// ---------------------------------------------------------------- token system
function ringOf(o) { return o === 'SOL' ? 0 : STABLES.has(o) ? 1 : 2; }
function buildBodies(pairs, t) {
  bodies.length = 0; if (!bodyGeo) return;
  const top = pairs.filter(p => p.liq > 0).slice(0, MAXB);
  const maxL = top.length ? Math.log10(top[0].liq) : 3;
  const pos = bodyGeo.attributes.position.array, aO = bodyGeo.attributes.aO.array, aC = bodyGeo.attributes.aC.array, aS = bodyGeo.attributes.aS.array, aD = bodyGeo.attributes.aD.array;
  top.forEach((p, i) => {
    const o = symOf(p.other), ring = ringOf(o), L = Math.log10(p.liq);
    const R = ring === 0 ? 9 + (i % 4) * 0.9 : ring === 1 ? 15 + (i % 3) * 1.1 : 22 + fr(i * 7.31) * 20 + (i / MAXB) * 8;
    const b = { p, o, ring, R, a0: i * 2.399, inc: ring === 0 ? .22 : ring === 1 ? -.14 : (fr(i * 3.7) - .5) * .5, L };
    bodies.push(b);
    const j = (Math.floor(fr(i * .6180339) * N)) | 0; pos.set([G.g[j * 3] * 1.4, G.g[j * 3 + 1], G.g[j * 3 + 2] * 1.4], i * 3);
    aO.set([R, b.a0, b.inc, 0], i * 4);
    aC.set(ring === 0 ? [.75, 1, .55] : ring === 1 ? [.62, .85, 1] : [1, .78, .45], i * 3);
    aS[i] = 1.2 + Math.pow(Math.max(0, L - 2.5), 1.7) * 1.25 * (5.5 / Math.max(5.5, maxL)) * 1.1; aD[i] = i / Math.max(1, top.length);
  });
  for (const k of ['position', 'aO', 'aC', 'aS', 'aD']) bodyGeo.attributes[k].needsUpdate = true;
  bodyGeo.setDrawRange(0, top.length);
  S.bodiesT0 = t; S.starT0 = Math.min(S.starT0 > NOW - 3 ? S.starT0 : t, t);
}

// ---------------------------------------------------------------- overlay: token panel, list, drawer
const LABN = () => isMobile() ? 4 : 9;
function renderLabels() { $('labs').innerHTML = bodies.slice(0, LABN()).map(b => `<div class="lab">${logo(b.p.other?.mint, b.o, 16)} ${esc(b.o)} <b>· ${esc(dexName(b.p.dex))} · ${usd(b.p.liq)}</b></div>`).join(''); }
function pairRow(p) {
  return `<button type="button" class="row${p._new ? ' new' : ''}" data-m="${esc(p.other?.mint || '')}"><span class="p">${logo(p.other?.mint, symOf(p.other), 18)}${esc(symOf(S.tok?.token ? { mint: S.tokMint, sym: S.tok.token.symbol } : null))} <em>/ ${esc(symOf(p.other))}</em></span><span class="d" style="--c:${dexCol(p.dex)}"><i></i>${esc(dexName(p.dex))}</span><span class="v">${usd(p.liq)}</span></button>`;
}
function renderToken() {
  const d = S.tok; if (!d) return; const t = d.token;
  const sym = t.symbol || (t.mint === SOL ? 'SOL' : short(t.mint));
  const tl = $('tLogo'); if (tl.dataset.m !== t.mint || (t.symbol && tl.dataset.s !== t.symbol)) { tl.innerHTML = logo(t.mint, sym, isMobile() ? 40 : 56); tl.dataset.m = t.mint; tl.dataset.s = t.symbol || ''; }
  const el = $('tSym'); el.textContent = sym; el.style.fontSize = '';
  const L = [...sym].length; if (L > 5) el.style.fontSize = `min(${Math.round(120 * 5.2 / L)}px, ${isMobile() ? 64 : 120}px)`;
  $('tName').textContent = t.name && t.name !== sym ? t.name : '';
  const pr = d.price; let px = '—';
  if (pr && pr.usd) { const ch = pr.change_h24; px = priceHTML(pr.usd) + (ch != null ? `<span class="ch ${ch >= 0 ? 'up' : 'dn'}">${ch >= 0 ? '+' : ''}${ch.toFixed(2)}% 24h</span>` : '') + `<span class="src">${pr.source === 'dexscreener' ? 'via DexScreener' : 'last snapshot ' + esc(ageOf(pr.at))}</span>`; }
  else if (d.loadingPrice) px = '';
  $('tPx').innerHTML = px;
  $('m1').textContent = nf(t.pairs_total); $('m2').textContent = usd(t.liq_total); $('m3').textContent = usd(t.vol_total);
  $('tMintAddr').textContent = short(t.mint, 6, 6); $('tMint').dataset.m = t.mint;
  $('tFirst').textContent = t.first_pool ? `first pool ${new Date(t.first_pool).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : '';
  const st = d.enrich?.state; const E = $('tEnr');
  E.innerHTML = st === 'queued' || st === 'running' ? `<i></i>${t.pairs_total ? 'Refreshing' : 'Mapping'} pairs from DexScreener · GeckoTerminal · Raydium — first lookups take ~1–2 min`
    : st === 'limited' || st === 'busy' ? 'Showing stored pairs (refresh queue busy)'
      : t.last_enriched ? `Pairs refreshed ${ageOf(t.last_enriched)}${ageOf(t.last_enriched) === 'just now' ? '' : ' ago'}` : '';
  const pairs = S.pairs; const n = t.pairs_total;
  $('lSub').textContent = n ? `${nf(n)} total · by liquidity` : '';
  $('rows').innerHTML = pairs.length ? pairs.slice(0, isMobile() ? 0 : 9).map(pairRow).join('') : `<div class="empty">${st === 'queued' || st === 'running' ? 'Looking for pools across Solana DEXes…' : 'No pools found for this token yet.'}</div>`;
  $('more').textContent = n ? `View all ${nf(n)} pairs →` : ''; $('more').style.display = n ? '' : 'none';
  $('pill').textContent = n ? `${nf(n)} pairs · ${usd(t.liq_total)} liquidity — view all ↑` : 'No pools yet';
  document.title = `${sym} — every pair · Pairs`;
  renderLabels();
}

// drawer
const SORTK = { liq: p => p.liq ?? -1, vol: p => p.vol ?? -1, age: p => Date.parse(p.created_at || 0) || 0, dex: p => dexName(p.dex), quote: p => symOf(p.quote), pair: p => symOf(p.other) };
function drawerRows() {
  const { k, asc } = S.sort; const f = SORTK[k] || SORTK.liq; const arr = [...S.drawerPairs];
  arr.sort((a, b) => { const x = f(a), y = f(b); const r = typeof x === 'string' ? x.localeCompare(y) : (x - y); return asc ? r : -r; });
  if (k === 'age' && asc === false) { } // newest first
  return arr;
}
function renderDrawer() {
  const isNew = S.drawer === 'new'; const t = S.tok?.token;
  $('dTitle').innerHTML = isNew ? `New <em>today</em>` : `${esc(t?.symbol || short(S.tokMint))} <em>pairs</em>`;
  $('dSub').textContent = isNew ? `Freshly created pools the poller saw in the last 3 hours · newest first` : `${nf(t?.pairs_total)} pools · ${usd(t?.liq_total)} liquidity · ${short(S.tokMint, 6, 6)}`;
  document.querySelectorAll('#drawer th').forEach(th => { th.classList.toggle('on', th.dataset.k === S.sort.k); th.classList.toggle('asc', th.dataset.k === S.sort.k && S.sort.asc); });
  const rows = drawerRows();
  $('dRows').innerHTML = rows.map(p => {
    const a = isNew ? p.base : p.other, b = isNew ? p.quote : null;
    const label = isNew ? `${esc(symOf(p.base))} <em>/ ${esc(symOf(p.quote))}</em>` : `${esc(t?.symbol || '')} <em>/ ${esc(symOf(p.other))}</em>`;
    return `<tr class="${p._new ? 'new' : ''}"><td class="pr"><button type="button" data-m="${esc(a?.mint || '')}">${logo(a?.mint, symOf(a), 20)}${label}</button></td>
     <td><span class="dx" style="--c:${dexCol(p.dex)}"><i></i>${esc(dexName(p.dex))}</span></td><td>${esc(symOf(p.quote))}</td>
     <td class="n">${usd(p.liq)}</td><td class="n">${usd(p.vol)}</td><td class="n">${ageOf(p.created_at)}</td>
     <td class="n"><a href="https://dexscreener.com/solana/${esc(p.pool)}" target="_blank" rel="noopener noreferrer"><span class="lg">DexScreener</span><span class="sm">DS</span> ↗</a><a href="https://solscan.io/account/${esc(p.pool)}" target="_blank" rel="noopener noreferrer"><span class="lg">Solscan</span><span class="sm">Scan</span> ↗</a></td></tr>`;
  }).join('') || `<tr><td colspan="7" style="color:var(--mut);padding:24px 8px">Nothing here yet.</td></tr>`;
  $('dNote').textContent = isNew ? 'Live: new pools arrive as the ingest poller sees them (GeckoTerminal new-pools feed, ~every 2 min).'
    : (S.tok?.truncated ? `Showing the top ${nf(S.drawerPairs.length)} of ${nf(t?.pairs_total)} by ${S.sort.k === 'vol' ? 'volume' : S.sort.k === 'age' ? 'age' : 'liquidity'}. ` : '') + 'Liquidity and volume are the latest stored snapshot per pool (DexScreener, GeckoTerminal, Raydium).';
}
async function openDrawer(kind) {
  S.drawer = kind; $('drawer').classList.add('open');
  if (kind === 'new') {
    S.sort = { k: 'age', asc: false }; S.drawerPairs = [];
    renderDrawer();
    try { const d = await api('/api/v1/new?limit=200'); S.drawerPairs = d.pools.map(newToPair); } catch (e) { }
  } else { S.sort = { k: 'liq', asc: false }; S.drawerPairs = S.pairs; }
  renderDrawer();
}
function closeDrawer() { S.drawer = null; $('drawer').classList.remove('open'); }
const newToPair = r => ({ pool: r.address, dex: r.dex, base: { mint: r.base, sym: r.base_sym || (r.name || '').split('/')[0]?.trim() }, quote: { mint: r.quote, sym: r.quote_sym || (r.name || '').split('/')[1]?.trim() }, liq: r.liq, vol: r.vol, created_at: r.created_at });
document.querySelectorAll('#drawer th[data-k]').forEach(th => th.addEventListener('click', async () => {
  const k = th.dataset.k; S.sort = { k, asc: S.sort.k === k ? !S.sort.asc : (k === 'pair' || k === 'dex' || k === 'quote') };
  if (S.drawer === 'token' && S.tok?.truncated && ['liq', 'vol', 'age'].includes(k)) {
    const sv = k === 'age' ? (S.sort.asc ? 'oldest' : 'age') : k;
    try { const d = await api(`/api/v1/token/${S.tokMint}?sort=${sv}&limit=1000`); S.drawerPairs = d.pairs; } catch (e) { }
  }
  renderDrawer();
}));
$('dClose').onclick = closeDrawer; $('more').onclick = () => openDrawer('token'); $('pill').onclick = () => S.tok?.token?.pairs_total && openDrawer('token');
$('navN').onclick = e => { e.preventDefault(); openDrawer('new'); };
document.addEventListener('click', e => {
  const b = e.target.closest('[data-m]'); if (!b || b.id === 'tMint') return; const m = b.dataset.m;
  if (m && m.length >= 32 && (b.closest('#feed') || b.closest('#arr'))) { e.preventDefault(); focusAndOpen(m); }
  else if (m && m.length >= 32 && (b.closest('#rows') || b.closest('#dRows'))) { e.preventDefault(); closeDrawer(); focusToken(m); }
});
$('tMint').onclick = () => { navigator.clipboard?.writeText($('tMint').dataset.m).then(() => toast('CA copied'), () => { }); };
$('back').onclick = () => unfocus(true);
$('home').onclick = e => { e.preventDefault(); closeDrawer(); unfocus(true); };
$('navU').onclick = e => { e.preventDefault(); closeDrawer(); unfocus(true); };

// ---------------------------------------------------------------- focus / unfocus + history
let pollTimer = null, reqId = 0;
async function focusToken(mint, { push = true, hint = null } = {}) {
  sound.select();
  if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) return;
  const id = ++reqId; const t = NOW;
  if (push && new URLSearchParams(location.search).get('t') !== mint) history.pushState({ t: mint }, '', `/?t=${mint}`);
  const already = focusVal(t) > .95 && S.tokMint;
  S.tokMint = mint; document.body.classList.add('focus');
  if (!already) S.focus = { from: focusVal(t), to: 1, t0: t, dur: RM ? 0.8 : 2.6 };
  S.starT0 = already ? t - 0.2 : t + (RM ? 0 : 1.05); S.bodiesOut.t0 = -99;
  S.tok = { token: { mint, symbol: hint?.symbol || null, name: hint?.name || null, pairs_total: hint?.pairs ?? null, liq_total: hint?.liq ?? null }, enrich: null, loadingPrice: true, pairs: [] };
  S.pairs = []; buildBodies([], t); renderToken(); S.uiT0 = t;
  clearTimeout(pollTimer);
  const load = async (first) => {
    let d; try { d = await api(`/api/v1/token/${mint}`); } catch (e) { if (id === reqId) { $('tEnr').textContent = 'Could not load this token right now.'; } return; }
    if (id !== reqId) return;
    const hadPairs = S.pairs.length; S.tok = d; S.pairs = d.pairs;
    if (gm) gm.uniforms.uTok.value = d.token.ht;
    if (first || (!hadPairs && d.pairs.length) || d.pairs.length !== hadPairs) buildBodies(d.pairs, Math.max(NOW, already ? NOW : S.focus.t0 + (RM ? .2 : 1.1)));
    renderToken(); if (S.drawer === 'token') { S.drawerPairs = S.pairs; renderDrawer(); }
    const st = d.enrich?.state; if (st === 'queued' || st === 'running') pollTimer = setTimeout(() => load(false), 5000);
  };
  return load(true);
}
// fly to a token and open its full pairs drawer (new-pairs list rows, new arrivals in the galaxy)
async function focusAndOpen(mint) {
  closeDrawer(); const p = focusToken(mint);
  await Promise.all([p, new Promise(r => setTimeout(r, RM ? 200 : 1500))]);
  if (S.tokMint === mint) openDrawer('token');
}
function unfocus(push) {
  if (!S.tokMint && focusVal(NOW) < .01) return;
  clearTimeout(pollTimer); reqId++;
  const t = NOW; S.focus = { from: focusVal(t), to: 0, t0: t, dur: RM ? 0.6 : 2.2 }; S.bodiesOut.t0 = t; S.tokMint = null;
  document.body.classList.remove('focus'); document.title = 'Pairs — the Solana liquidity universe';
  if (push && location.search) history.pushState({}, '', '/');
}
addEventListener('popstate', () => { const m = new URLSearchParams(location.search).get('t'); closeDrawer(); if (m) focusToken(m, { push: false }); else unfocus(false); });

// ---------------------------------------------------------------- search + autocomplete
const qEl = $('q'), sugg = $('sugg'); let sres = [], sOn = 0, sTimer = null, sSeq = 0;
function renderSugg(q) {
  if (!sres.length) { sugg.innerHTML = q ? `<div class="hint">No token named “${esc(q)}” yet — paste its mint address to map it</div>` : ''; sugg.classList.toggle('show', !!q); return; }
  sugg.innerHTML = sres.map((r, i) => `<div class="it${i === sOn ? ' on' : ''}" role="option" data-i="${i}">${logo(r.mint, r.symbol, 28, 'av')}
   <div>${esc(r.symbol || short(r.mint))}${r.collision ? '<span class="tw">SAME TICKER</span>' : ''}<small>${esc(r.name || '')}${r.name ? ' · ' : ''}${esc(short(r.mint, 4, 4))}</small></div>
   <div class="rt">${r.unmapped ? 'not mapped yet<br><b>map it →</b>' : `<b>${usd(r.liq)}</b><br>${nf(r.pairs)} pool${r.pairs === 1 ? '' : 's'}`}</div></div>`).join('')
    + (sres.some(r => r.collision) ? `<div class="hint">Several tokens share this ticker — ranked by liquidity, check the mint</div>` : '');
  sugg.classList.add('show');
}
async function doSearch(q) {
  const seq = ++sSeq; if (!q) { sres = []; renderSugg(''); return; }
  try { const d = await api(`/api/v1/search?q=${encodeURIComponent(q)}`); if (seq !== sSeq) return; sres = d.results; sOn = 0; renderSugg(q); } catch (e) { }
}
qEl.addEventListener('input', () => { clearTimeout(sTimer); const q = qEl.value.trim(); sTimer = setTimeout(() => doSearch(q), CAP ? 0 : 140); });
qEl.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (!sres.length) return; sOn = (sOn + (e.key === 'ArrowDown' ? 1 : -1) + sres.length) % sres.length; renderSugg(qEl.value.trim()); }
  else if (e.key === 'Escape') { sugg.classList.remove('show'); qEl.blur(); }
});
function pick(i) { const r = sres[i]; if (!r) return; sugg.classList.remove('show'); qEl.value = ''; qEl.blur(); closeDrawer(); focusToken(r.mint, { hint: r }); }
$('search').addEventListener('submit', e => { e.preventDefault(); const q = qEl.value.trim(); if (sres.length) pick(sOn); else if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(q)) { qEl.value = ''; focusToken(q); } });
sugg.addEventListener('pointerdown', e => { const it = e.target.closest('.it'); if (it) { e.preventDefault(); pick(+it.dataset.i); } });
qEl.addEventListener('blur', () => setTimeout(() => sugg.classList.remove('show'), 120));
qEl.addEventListener('focus', () => { if (qEl.value.trim()) renderSugg(qEl.value.trim()); });
addEventListener('keydown', e => {
  if ((e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) && document.activeElement !== qEl) { e.preventDefault(); qEl.focus(); }
  else if (e.key === 'Escape' && document.activeElement !== qEl) { if (S.drawer) closeDrawer(); else if (S.tokMint) unfocus(true); }
});
$('kbd').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : '/';

// ---------------------------------------------------------------- live new pairs (SSE, polling fallback)
const QUOTEM = new Set([SOL, 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', 'USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB']);
// ---- new pairs list: ~100 rows on load, live prepends, "+N new" pill, load-more paging, expand/collapse
const FEED_MAX = 600; const fList = $('fList'), fPill = $('fPill'), fMore = $('fMore'); let feedNew = 0, feedBefore = null, feedBusy = false;
const nameSym = (r, i) => (r.name || '').split('/')[i]?.trim() || null;
function rowTok(r) {
  const flip = QUOTEM.has(r.base) && r.quote && !QUOTEM.has(r.quote);
  const m = flip ? r.quote : r.base, qm = flip ? r.base : r.quote;
  const s = (flip ? r.quote_sym : r.base_sym) || nameSym(r, flip ? 1 : 0) || short(m || '????', 4, 3);
  const q = qm === SOL ? 'SOL' : ((flip ? r.base_sym : r.quote_sym) || nameSym(r, flip ? 0 : 1) || '?');
  return { m, s, q };
}
function agoS(ms) { const s = Math.max(0, (Date.now() - ms) / 1000); return s < 60 ? `${Math.floor(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`; }
function feedRow(r) {
  const k = rowTok(r), ms = Date.parse(r.created_at) || Date.now();
  const el = document.createElement('button'); el.type = 'button'; el.className = 'fr'; el.dataset.m = k.m || ''; el.title = `${k.s} / ${k.q} · ${dexName(r.dex)} — open all pairs`;
  el.innerHTML = `${logo(k.m, k.s, 22)}<span class="fs"><b>${esc(k.s)}</b><em>/ ${esc(k.q)}</em></span><span class="fd" style="--c:${dexCol(r.dex)}"><i></i>${esc(dexName(r.dex))}</span><span class="fv">${r.liq != null ? usd(r.liq) : '—'}</span><span class="fa" data-t="${ms}">${agoS(ms)}</span>`;
  return el;
}
function feedCount() { $('fN').textContent = fList.children.length ? `${nf(fList.children.length)}` : ''; }
function feedPrepend(rows) { // rows: newest first
  if (!rows.length) return;
  const atTop = fList.scrollTop < 8, h0 = fList.scrollHeight, frag = document.createDocumentFragment();
  rows.forEach(r => { const el = feedRow(r); if (atTop) el.classList.add('in'); frag.appendChild(el); });
  fList.prepend(frag);
  while (fList.children.length > FEED_MAX) fList.lastElementChild.remove();
  if (!atTop) { fList.scrollTop += fList.scrollHeight - h0; feedNew += rows.length; fPill.textContent = `+${feedNew} new ↑`; fPill.classList.add('show'); }
  feedCount();
}
function feedAppend(rows) { const frag = document.createDocumentFragment(); rows.forEach(r => frag.appendChild(feedRow(r))); fList.appendChild(frag); feedCount(); }
async function feedLoadMore() {
  if (feedBusy || !feedBefore) return; feedBusy = true; fMore.textContent = 'Loading…';
  try { const d = await api(`/api/v1/new?before=${feedBefore}&limit=100`); const rows = d.pools.filter(r => !S.seen?.has(r.address)); rows.forEach(r => (S.seen ||= new Set()).add(r.address)); feedAppend(rows); feedBefore = d.next_before; }
  catch (e) { }
  feedBusy = false; fMore.textContent = feedBefore ? 'Load more' : 'That’s everything'; fMore.disabled = !feedBefore;
}
fMore.onclick = feedLoadMore;
fList.addEventListener('scroll', () => {
  if (fList.scrollTop < 8 && feedNew) { feedNew = 0; fPill.classList.remove('show'); }
  if (fList.scrollTop + fList.clientHeight > fList.scrollHeight - 160 && fList.children.length < FEED_MAX) feedLoadMore();
}, { passive: true });
fPill.onclick = () => { fList.scrollTo({ top: 0, behavior: RM ? 'auto' : 'smooth' }); feedNew = 0; fPill.classList.remove('show'); };
const setTall = on => { $('feed').classList.toggle('tall', on); document.body.classList.toggle('feedtall', on); $('fExp').textContent = on ? 'Collapse' : 'Expand'; $('fExp').setAttribute('aria-expanded', on); try { localStorage.setItem('pv.feedTall', on ? '1' : ''); } catch (e) { } };
$('fExp').onclick = () => setTall(!$('feed').classList.contains('tall'));
try { if (localStorage.getItem('pv.feedTall') && !isMobile()) setTall(true); } catch (e) { }
setInterval(() => { if (document.hidden) return; const top = fList.scrollTop, bot = top + fList.clientHeight + 40; for (const el of fList.children) { if (el.offsetTop > bot) break; if (el.offsetTop + 40 < top) continue; const a = el.lastElementChild; a.textContent = agoS(+a.dataset.t); } }, 1000);
function ingest(rows, flash) { // rows: oldest first
  const fresh = [];
  for (const r of rows) {
    if (S.seen?.has(r.address)) continue; (S.seen ||= new Set()).add(r.address);
    r.tokMint = rowTok(r).m;
    S.feed.unshift(r); fresh.push(r); if (flash) S.novaQ.push(r);
    // a pair of the focused token: add it to the list as NEW
    if (S.tokMint && (r.base === S.tokMint || r.quote === S.tokMint)) {
      const p = newToPair(r); p.other = r.base === S.tokMint ? p.quote : p.base; p._new = true; S.pairs.unshift(p); S.tok.token.pairs_total = (S.tok.token.pairs_total || 0) + 1; renderToken();
    }
  }
  S.feed.length = Math.min(S.feed.length, 60);
  feedPrepend(fresh.reverse());
  S.cursor = Math.max(S.cursor, ...rows.map(r => r.rid || 0));
}
const nw = await newP;
ingest([...nw.pools].reverse(), false);
feedBefore = nw.next_before ?? (nw.pools.length ? Math.min(...nw.pools.map(r => r.rid)) : null);
fMore.textContent = feedBefore ? 'Load more' : ''; fMore.disabled = !feedBefore;
if (fList.children.length < 60 && feedBefore) feedLoadMore();
S.novaQ.push(...S.feed.slice(0, 4).reverse()); // replay the latest births so the sky is never silent on arrival
function startLive() {
  let es = null, fails = 0;
  const poll = async () => { try { const d = await api(`/api/v1/new?after=${S.cursor}&limit=50`); if (d.pools.length) ingest([...d.pools].reverse(), true); } catch (e) { } setTimeout(poll, 20000); };
  if (!window.EventSource) return poll();
  es = new EventSource('/api/v1/live');
  es.addEventListener('pools', e => { fails = 0; try { ingest(JSON.parse(e.data), true); } catch (x) { } });
  es.onerror = () => { if (++fails > 3) { es.close(); poll(); } };
}
startLive();
setInterval(async () => { try { S.stats = await api('/api/v1/stats'); paintStats(); } catch (e) { } }, 60000);

// supernova: place a newborn pool where its token lives in the galaxy (or on screen while focused)
function spawnNova(r, t) {
  let i = -1;
  if (liveN < CAPN && renderer) { i = liveN++; layout(Math.log10(Math.max(150, r.liq || 150)), r.dexi ?? 10, r.ht ?? Math.random(), r.hp ?? Math.random(), G, i); G.b[i] = t;
    const ga = galaxy.geometry.attributes; for (const k of ['position', 'aF', 'aC', 'aS', 'aR', 'aT', 'aB']) { ga[k].addUpdateRange?.(i * ga[k].itemSize, ga[k].itemSize); ga[k].needsUpdate = true; } galaxy.geometry.setDrawRange(0, liveN); }
  S.nova = { r, t0: t, i, fixed: null };
  addArrival(r, S.nova, t);
  sound.ping();
}
// new arrivals stay clickable for a minute: pulsing ring + hover label (logo · symbol), click = fly + open drawer
const ARR_S = 60; S.arr = [];
function addArrival(r, nv, t) {
  const k = rowTok(r); if (!k.m) return;
  const el = document.createElement('button'); el.type = 'button'; el.className = 'arr'; el.dataset.m = k.m; el.setAttribute('aria-label', `New pair ${k.s} / ${k.q}: open all pairs`);
  el.innerHTML = `<span class="al">${logo(k.m, k.s, 18)}<b>${esc(k.s)}</b><em>/ ${esc(k.q)} · ${esc(dexName(r.dex))}</em></span>`;
  $('arr').appendChild(el); S.arr.push({ el, nv, t0: t });
  while (S.arr.length > 14) S.arr.shift().el.remove();
}
function placeArrivals(t) {
  const fv = focusVal(t);
  for (let j = S.arr.length - 1; j >= 0; j--) {
    const a = S.arr[j], age = t - a.t0;
    if (age > ARR_S) { a.el.remove(); S.arr.splice(j, 1); continue; }
    let vis = false, x = 0, y = 0;
    if (a.nv.i >= 0 && fv < .5) { let z; [x, y, z] = toScreen(novaWorld(a.nv, t)); vis = z < 1 && x > 8 && y > 70 && x < innerWidth - 8 && y < innerHeight - 70; }
    a.el.style.transform = `translate(${x}px,${y}px)`;
    a.el.style.opacity = vis ? Math.min(1, age * 1.5) * (1 - seg(age, ARR_S - 8, ARR_S)) : 0;
    a.el.style.pointerEvents = vis ? 'auto' : 'none';
  }
}
function novaWorld(n, t) {
  if (focusVal(t) > .5 || n.i < 0) { if (!n.fixed) { const sx = isMobile() ? .5 : .14 + Math.random() * .1, sy = isMobile() ? .62 : .64 + Math.random() * .1; n.fixed = V(sx * 2 - 1, -(sy * 2 - 1), .5).unproject(camera).sub(camera.position).normalize().multiplyScalar(80).add(camera.position); } return n.fixed; }
  const x = G.g[n.i * 3], y = G.g[n.i * 3 + 1], z = G.g[n.i * 3 + 2], r = Math.hypot(x, z), a = t * (RM ? .1 : 1) * 0.9 / (r + 6), c = Math.cos(a), s = Math.sin(a);
  return galaxy.localToWorld(V(c * x + s * z, y, -s * x + c * z));
}

// ---------------------------------------------------------------- sound (off by default; deep-space soundscape, see space-audio.js)
const sound = (() => {
  let ctx = null, sp = null, timer = null;
  function on() {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    sp = createSpace(ctx, { seed: (Date.now() & 0xffff) | 1 });
    sp.output.connect(ctx.destination); sp.start(); sp.fadeTo(1, 4);
    timer = setInterval(() => sp && sp.tick(6), 2000);
  }
  function off() {
    clearInterval(timer); timer = null;
    const c = ctx, s = sp; ctx = null; sp = null;
    if (c && s) { s.fadeTo(0, .6); setTimeout(() => c.close(), 800); }
  }
  const live = () => S.sound && ctx && sp && ctx.state === 'running';
  return {
    toggle() { S.sound = !S.sound; if (S.sound) on(); else off(); },
    ping() { if (live()) sp.nova(); },
    hover() { if (live()) sp.hover(); },
    select() { if (live()) sp.select(); },
  };
})();
// subtle interaction sounds: hover a token/pool row or search suggestion, click to select
let hovEl = null;
document.addEventListener('pointerover', e => {
  const el = e.target.closest?.('#rows [data-m], #dRows [data-m], #feed [data-m], #sugg .it, #dRows tr, #rows tr');
  if (el && el !== hovEl) { hovEl = el; sound.hover(); } else if (!el) hovEl = null;
});
$('snd').onclick = () => { sound.toggle(); $('snd').classList.toggle('on', S.sound); $('snd').setAttribute('aria-pressed', S.sound); $('snd').querySelector('em').textContent = S.sound ? 'Sound on' : 'Sound off'; };

// ---------------------------------------------------------------- input: magnetic cursor, drag orbit, scroll dive
const cur = $('cur'); let magEl = null, cx = innerWidth / 2, cy = innerHeight / 2, tx = cx, ty = cy;
addEventListener('pointermove', e => {
  S.mouse.x = e.clientX / innerWidth; S.mouse.y = e.clientY / innerHeight; tx = e.clientX; ty = e.clientY;
  magEl = e.target.closest?.('[data-mag]') || null; cur.classList.toggle('big', !!magEl); cur.style.opacity = 1;
  if (S.drag) { const dx = e.clientX - S.drag.x; S.drag.x = e.clientX; S.yawV = -dx * 0.004; S.yaw += -dx * 0.004; }
});
const glc = $('gl');
glc.addEventListener('pointerdown', e => { S.drag = { x: e.clientX }; glc.setPointerCapture(e.pointerId); });
glc.addEventListener('pointerup', () => { S.drag = null; });
glc.addEventListener('wheel', e => { if (focusVal(NOW) > .5) return; e.preventDefault(); S.distT = clamp(S.distT * (1 + e.deltaY * 0.0012), 0.32, 1.35); }, { passive: false });

// ---------------------------------------------------------------- FPS governor (real devices only)
let fpsAcc = 0, fpsN = 0, govLevel = 0;
function govern(dt, t) {
  if (CAP || !renderer || t < S.introEnd + 1) return;
  fpsAcc += dt; fpsN++; if (fpsN < 90) return; const avg = fpsAcc / fpsN; fpsAcc = 0; fpsN = 0;
  if (avg > 0.026 && govLevel < 3) {
    govLevel++;
    if (govLevel === 1) { renderer.setPixelRatio(Math.max(1, renderer.getPixelRatio() * .75)); onResize(); }
    else if (govLevel === 2) { bloom.enabled = false; }
    else { galaxy.geometry.setDrawRange(0, Math.floor(liveN / 2)); }
  }
}

// ---------------------------------------------------------------- frame
const camPos = new THREE.Vector3(), camLook = new THREE.Vector3(), fPos = new THREE.Vector3(), fLook = new THREE.Vector3();
const blocked = (x, y, w) => {
  if (y > innerHeight - (isMobile() ? 150 : 110) || y < 60) return true;
  if (isMobile()) return y < 330;
  return x + (w || 200) > innerWidth - 420 || (x < 470 && y < 470);
};
const deep = QS.get('t');
let deepFired = false;
onFrame((t, dt) => {
  const intro = S.introEnd, F = ease.io(clamp(focusVal(t), 0, 1)), Fr = focusVal(t);
  // overlay choreography (intro)
  chars.forEach((c, i) => { const a = seg(t, 0.5 + i * 0.035, 1.3 + i * 0.035), o = seg(t, 2.7 + i * 0.012, 3.3 + i * 0.012); const ea = ease.outExpo(a), eo = ease.io(o);
    if (RM) { c.style.opacity = 0; return; } c.style.transform = `translate3d(0,${(1 - ea) * 105 - eo * 60}%,0) rotate(${(1 - ea) * 8}deg)`; c.style.opacity = Math.min(ea, 1 - eo); c.style.filter = eo > 0 ? `blur(${eo * 8}px)` : ''; });
  op($('kicker'), RM ? 0 : Math.min(seg(t, 1.6, 2.2), 1 - seg(t, 2.8, 3.2)));
  const it = RM ? 99 : t; // reduced motion: show chrome immediately
  op($('top'), seg(it, 3.0, 3.8));
  const ovA = ease.out(seg(it, 3.6, 4.4)) * (1 - seg(Fr, 0, .3)); op($('ov'), ovA); $('ov').style.transform = `translateY(${(1 - ease.out(seg(it, 3.6, 4.4))) * 20}px)`;
  if (S.stats) $('ovn').textContent = nf((S.stats.pools || 0) * ease.out(seg(it, 3.6, 5.2)));
  op($('legend'), seg(it, 4.0, 4.8) * (1 - seg(Fr, 0, .3)));
  const sA = ease.out(seg(it, 4.2, 5.0)); op($('search'), sA); $('search').style.transform = `translateY(${(1 - sA) * 24}px)`; $('search').style.visibility = sA > 0 ? 'visible' : 'hidden';
  op($('feed'), seg(it, 4.8, 5.4)); op(ld, 1 - seg(t, 0, .6));
  if (t > 1) ld.style.display = 'none';
  if (deep && !deepFired && t > (RM ? 0 : 3.2)) { deepFired = true; focusToken(deep, { push: false }); }
  // focus UI
  const tA = S.tokMint ? ease.outExpo(seg(t, S.uiT0 + (RM ? 0 : 1.4), S.uiT0 + (RM ? .3 : 2.4))) : 1 - seg(t, S.focus.t0, S.focus.t0 + .5);
  const tokEl = $('tok'); op(tokEl, tA); tokEl.style.transform = `translateY(${(1 - tA) * 30}px)`; tokEl.style.visibility = tA > 0.01 ? 'visible' : 'hidden';
  const lst = $('list'); op(lst, tA); lst.style.visibility = tA > 0.01 ? 'visible' : 'hidden';
  [...lst.querySelectorAll('.row')].forEach((r, i) => { const a = S.tokMint ? ease.out(seg(t, S.uiT0 + 1.8 + i * .07, S.uiT0 + 2.3 + i * .07)) : 1; r.style.opacity = a; r.style.transform = `translateX(${(1 - a) * 24}px)`; });
  op($('pill'), tA);
  // cursor
  if (FINE && !CAP) { let gx = tx, gy = ty; if (magEl) { const r = magEl.getBoundingClientRect(); gx += (r.left + r.width / 2 - tx) * .35; gy += (r.top + r.height / 2 - ty) * .35; } cx += (gx - cx) * Math.min(1, dt * 18); cy += (gy - cy) * Math.min(1, dt * 18); cur.style.transform = `translate(${cx}px,${cy}px)`; }
  // nova scheduling
  if (!S.nova && S.novaQ.length && t > Math.max(intro + 1.2, 4.6) && t >= S.nextNovaT) { spawnNova(S.novaQ.shift(), t); S.nextNovaT = t + clamp(100 / Math.max(1, S.novaQ.length + 1), 3.2, 14); }
  const nv = $('nova');
  if (!renderer) {
    if (S.nova) { const k = t - S.nova.t0; nv.style.transform = `translate(${innerWidth * .5}px,${innerHeight * .4}px)`; op(nv, Math.min(seg(k, .15, .45), 1 - seg(k, 2.4, 2.9))); if (k > 3) S.nova = null; $('novaName').textContent = S.nova?.r.name || ''; }
    return;
  }
  // ---------- WebGL path
  const U = gm.uniforms; U.uT.value = t; U.uRT.value = RM ? t * .1 : t; fin.uniforms.uT.value = t;
  fin.uniforms.uFade.value = RM ? 1 : ease.out(seg(t, 0, 0.5));
  U.uReveal.value = RM ? 130 : 2 + ease.io(seg(t, 0.2, 3.4)) * 120;
  U.uFocus.value = clamp(Fr, 0, 1);
  const bIn = seg(t, S.bodiesT0, S.bodiesT0 + 2.1), bOut = S.bodiesOut.t0 > 0 ? seg(t, S.bodiesOut.t0, S.bodiesOut.t0 + .9) : 0;
  bm.uniforms.uFocus.value = bIn; bm.uniforms.uOut.value = bOut;
  if (bOut >= 1 && S.bodiesOut.t0 > 0 && !S.tokMint) { bodyGeo.setDrawRange(0, 0); bodies.length = 0; $('labs').innerHTML = ''; U.uTok.value = -1; S.bodiesOut.t0 = -99; }
  // camera
  S.dist += (S.distT - S.dist) * Math.min(1, dt * 4);
  if (!S.drag) { S.yaw += S.yawV; S.yawV *= Math.pow(0.04, dt); }
  baseCam(t, camPos, camLook);
  const fa = t * (RM ? .01 : .045) + 0.6 + S.yaw, R = isMobile() ? 66 : 44;
  fPos.set(Math.sin(fa) * R, (isMobile() ? 24 : 15) + Math.sin(t * .3) * 1.5, Math.cos(fa) * R);
  fLook.copy(isMobile() ? V(0, 7, 0) : V(-5.5, 0, 0).applyAxisAngle(Y, fa));
  camera.position.copy(camPos).lerp(fPos, F); camLook.lerp(fLook, F);
  if (FINE && !RM) { camera.position.x += (S.mouse.x - .5) * 2; camera.position.y += (S.mouse.y - .5) * -1.5; }
  camera.lookAt(camLook); camera.updateMatrixWorld();
  const sOn = S.tokMint ? ease.outExpo(seg(t, S.starT0, S.starT0 + .85)) * (1 + 1.4 * Math.exp(-Math.pow((t - S.starT0 - .2) * 3.5, 2))) : (1 - seg(t, S.focus.t0, S.focus.t0 + .8)) * (S.starT0 > 0 ? 1 : 0);
  star.material.uniforms.uI.value = Math.max(0, sOn) * .62;
  rings.forEach((l, j) => l.material.uniforms.uA.value = S.tokMint ? ease.io(seg(t, S.starT0 + .45 + j * .18, S.starT0 + 1.95 + j * .18)) : 1 - seg(t, S.focus.t0, S.focus.t0 + .7));
  const [sx, sy] = toScreen(V(0, 0, 0));
  fin.uniforms.uLens.value.set(sx / innerWidth, 1 - sy / innerHeight, S.tokMint ? ease.out(seg(t, S.starT0 - .25, S.starT0 + 1.15)) : (1 - seg(t, S.focus.t0, S.focus.t0 + .6)) * (fin.uniforms.uLens.value.z > 0 ? 1 : 0));
  // supernova
  if (S.nova) {
    const n = S.nova, k = (t - n.t0) / 1.6, p = novaWorld(n, t);
    novaMesh.position.copy(p); novaMesh.material.uniforms.uK.value = k <= 1 ? k : -1; novaMesh.material.uniforms.uS.value = Fr > .5 ? 12 : 14;
    U.uNova.value.set(p.x, p.y, p.z, k <= 1 ? k : -10);
    const [x, y, z] = toScreen(p); const r = n.r;
    if (n.lg !== 1) { n.lg = 1; const k = rowTok(r); $('novaLogo').innerHTML = logo(k.m, k.s, 26); }
    $('novaName').textContent = r.name || '?';
    $('novaMeta').textContent = `${dexName(r.dex)} · ${r.liq != null ? usd(r.liq) + ' liq · ' : ''}born ${new Date(r.created_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })} · ${ageOf(r.created_at)}${ageOf(r.created_at) === 'just now' ? '' : ' ago'}`;
    const edge = tmpV.copy(p).addScaledVector(tmpV2.setFromMatrixColumn(camera.matrixWorld, 0), novaMesh.material.uniforms.uS.value * .45); const [ex] = toScreen(edge); const rad = Math.abs(ex - x);
    const nx = clamp(x + rad + 14, 12, innerWidth - 300), ny = clamp(y - 22, 70, innerHeight - 170);
    nv.style.transform = `translate(${nx}px,${ny}px)`; op(nv, z < 1 ? Math.min(seg(t, n.t0 + .15, n.t0 + .45), 1 - seg(t, n.t0 + 2.4, n.t0 + 2.9)) : 0);
    if (t > n.t0 + 3) { S.nova = null; novaMesh.material.uniforms.uK.value = -1; U.uNova.value.w = -10; op(nv, 0); }
  }
  placeArrivals(t);
  // body labels
  const lA = S.tokMint ? seg(t, S.bodiesT0 + 1.3, S.bodiesT0 + 2.1) : 1 - bOut;
  const labEls = $('labs').children;
  for (let i = 0; i < labEls.length; i++) { const b = bodies[i]; if (!b) continue; const [x, y, z] = toScreen(orbitPos(b, t)); const el = labEls[i]; el.style.transform = `translate(${x + 6}px,${y - 7}px)`; op(el, z < 1 && !blocked(x, y, 200) ? lA * (0.55 + 0.45 * (i < 5)) : 0); }
  govern(dt, t);
  composer.render();
});

// ---------------------------------------------------------------- run
window.__app = { focusAndOpen, feedLoadMore, focusToken, unfocus, openDrawer, closeDrawer, S, type: async (q) => { qEl.focus(); qEl.value = q; qEl.dispatchEvent(new Event('input')); await doSearch(q); return sres; }, pick, flash: () => { const r = S.feed[0]; if (r) { S.nova = null; spawnNova(r, NOW); } }, tier, N };
if (CAP) { window.__step = t => step(t); window.__ready = true; }
else { const t0 = performance.now(); const loop = now => { step((now - t0) / 1000); requestAnimationFrame(loop); }; requestAnimationFrame(loop); }
