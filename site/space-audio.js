// Pairverse deep-space soundscape. Pure WebAudio synthesis, no samples.
// Works on any BaseAudioContext, so the live site and the offline preview render share this code.
//   const sp = createSpace(ctx); sp.output.connect(ctx.destination); sp.start(); sp.fadeTo(1, 3);
//   sp.hover(); sp.select(); sp.nova(); sp.tick(); // tick() schedules the rare bells ahead of time

function rng(seed) { let s = seed >>> 0 || 1; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

// Long, dark, slightly diffuse stereo impulse response (generated, not recorded).
function makeImpulse(ctx, seconds, decay, rnd) {
  const sr = ctx.sampleRate, n = Math.floor(sr * seconds), buf = ctx.createBuffer(2, n, sr);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch); let lp = 0;
    for (let i = 0; i < n; i++) {
      const t = i / n, env = Math.pow(1 - t, decay) * (i < sr * 0.02 ? i / (sr * 0.02) : 1);
      const k = 0.18 + 0.6 * (1 - t);           // darker as the tail goes on
      lp += k * ((rnd() * 2 - 1) - lp);
      d[i] = lp * env;
    }
  }
  return buf;
}

function noiseBuffer(ctx, seconds, rnd) {
  const n = Math.floor(ctx.sampleRate * seconds), buf = ctx.createBuffer(2, n, ctx.sampleRate);
  for (let ch = 0; ch < 2; ch++) { const d = buf.getChannelData(ch); let b = 0; for (let i = 0; i < n; i++) { b = 0.97 * b + 0.03 * (rnd() * 2 - 1); d[i] = b * 6; } } // brown-ish
  return buf;
}

function lfo(ctx, freq, depth, target, phase = 0) {
  // sine LFO with a start phase (radians), via a one-harmonic PeriodicWave
  const o = ctx.createOscillator(), g = ctx.createGain();
  if (phase) o.setPeriodicWave(ctx.createPeriodicWave(new Float32Array([0, Math.sin(phase)]), new Float32Array([0, Math.cos(phase)]), { disableNormalization: true }));
  o.frequency.value = freq; g.gain.value = depth; o.connect(g).connect(target); o.start();
  return o;
}

// G minor-ish palette: low, open and a little unresolved.
const DRONE = [49.0, 73.42, 98.0, 116.54, 146.83, 196.0, 293.66];
const BELLS = [783.99, 932.33, 1046.5, 1174.66, 1396.91, 1567.98, 1864.66, 2093.0];

export function createSpace(ctx, { seed = 7, level = 0.55 } = {}) {
  const rnd = rng(seed), now = () => ctx.currentTime, nodes = [];
  const output = ctx.createGain(); output.gain.value = 0;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -22; comp.knee.value = 18; comp.ratio.value = 3; comp.attack.value = 0.05; comp.release.value = 0.6;
  const bus = ctx.createGain(); bus.gain.value = level;
  bus.connect(comp).connect(output);

  const verb = ctx.createConvolver(); verb.buffer = makeImpulse(ctx, 9, 2.6, rnd);
  const wet = ctx.createGain(); wet.gain.value = 0.9; verb.connect(wet).connect(bus);
  const dry = ctx.createGain(); dry.gain.value = 0.55; dry.connect(bus);
  const send = (node, d = 0.5, w = 0.8) => { const a = ctx.createGain(), b = ctx.createGain(); a.gain.value = d; b.gain.value = w; node.connect(a).connect(dry); node.connect(b).connect(verb); };

  // 1. Drone: detuned saw/triangle pairs through a slowly sweeping low-pass.
  const droneLP = ctx.createBiquadFilter(); droneLP.type = 'lowpass'; droneLP.frequency.value = 720; droneLP.Q.value = 3;
  const droneG = ctx.createGain(); droneG.gain.value = 0.32; droneLP.connect(droneG); send(droneG, 0.6, 0.7);
  nodes.push(lfo(ctx, 0.021, 380, droneLP.frequency), lfo(ctx, 0.0073, 160, droneLP.frequency));
  DRONE.forEach((f, i) => {
    const vg = ctx.createGain(); vg.gain.value = (i === 0 ? 0.3 : 0.28) / (1 + i * 0.22);
    const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
    if (pan) { pan.pan.value = (i % 2 ? 1 : -1) * 0.25 * i / DRONE.length; nodes.push(lfo(ctx, 0.013 + i * 0.004, 0.35, pan.pan, i * 1.7)); vg.connect(pan).connect(droneLP); } else vg.connect(droneLP);
    // each voice breathes at its own very slow rate
    nodes.push(lfo(ctx, 0.017 + i * 0.0061, vg.gain.value * 0.6, vg.gain, i * 2.39));
    [-7, 6].forEach((det, k) => {
      const o = ctx.createOscillator(); o.type = i === 0 ? 'triangle' : (k ? 'sawtooth' : 'triangle');
      o.frequency.value = f; o.detune.value = det + (rnd() - 0.5) * 4;
      nodes.push(lfo(ctx, 0.05 + rnd() * 0.05, 3, o.detune));
      const g = ctx.createGain(); g.gain.value = o.type === 'sawtooth' ? 0.35 : 0.6; o.connect(g).connect(vg); nodes.push(o);
    });
  });
  // sub sine for weight
  const sub = ctx.createOscillator(); sub.frequency.value = 49; const subG = ctx.createGain(); subG.gain.value = 0.07;
  sub.connect(subG).connect(dry); nodes.push(sub);

  // 2. Cosmic wind: looped brown noise through a wandering band-pass, panned slowly.
  const nz = ctx.createBufferSource(); nz.buffer = noiseBuffer(ctx, 6, rnd); nz.loop = true;
  const bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 700; bp.Q.value = 1.4;
  const windG = ctx.createGain(); windG.gain.value = 0.11;
  nodes.push(lfo(ctx, 0.031, 480, bp.frequency, 2), lfo(ctx, 0.011, 250, bp.frequency), lfo(ctx, 0.023, 0.07, windG.gain, 5));
  nz.connect(bp).connect(windG); send(windG, 0.4, 0.9); nodes.push(nz);
  // a faint airy hiss on top
  const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 4200;
  const airG = ctx.createGain(); airG.gain.value = 0.012; nz.connect(hp).connect(airG); send(airG, 0.2, 1);

  // 3. Rare crystal bells: inharmonic sine partials, long tails, mostly reverb.
  function bell(t, f, amp = 0.05, pan = 0) {
    const g = ctx.createGain(); g.gain.value = 0;
    const p = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
    if (p) { p.pan.value = pan; g.connect(p); send(p, 0.18, 1.2); } else send(g, 0.18, 1.2);
    [[1, 1, 5.5], [2.76, 0.42, 3.2], [5.4, 0.2, 1.8], [8.93, 0.1, 1.1]].forEach(([r, a, dec]) => {
      const o = ctx.createOscillator(), og = ctx.createGain(); o.frequency.value = f * r; o.detune.value = (rnd() - 0.5) * 8;
      og.gain.setValueAtTime(0.0001, t); og.gain.exponentialRampToValueAtTime(amp * a, t + 0.012);
      og.gain.exponentialRampToValueAtTime(0.0001, t + dec);
      o.connect(og).connect(g); o.start(t); o.stop(t + dec + 0.1);
    });
    g.gain.setValueAtTime(1, t);
  }
  let nextBell = 0, scheduledTo = 0;
  function tick(ahead = 4, until = null) {
    const end = until ?? now() + ahead;
    if (!nextBell) nextBell = now() + 5 + rnd() * 5;
    while (nextBell < end) {
      const f = BELLS[Math.floor(rnd() * BELLS.length)], pan = (rnd() - 0.5) * 1.4;
      bell(nextBell, f, 0.03 + rnd() * 0.025, pan);
      if (rnd() < 0.35) bell(nextBell + 0.6 + rnd() * 1.4, BELLS[Math.floor(rnd() * BELLS.length)], 0.02, -pan);
      nextBell += 8 + rnd() * 11;
    }
    scheduledTo = end;
  }

  // 4. Interaction: soft sonar ping on hover, two-tone chime on select, deep sonar for a newborn pool.
  let lastHover = 0, lastNova = 0;
  function ping(t, f0, f1, dur, amp, w = 1.1) {
    const o = ctx.createOscillator(), g = ctx.createGain(); o.type = 'sine';
    o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f1, t + dur * 0.8);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(amp, t + 0.015); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); send(g, 0.35, w); o.start(t); o.stop(t + dur + 0.05);
  }
  return {
    output, tick,
    start(t = now()) { nodes.forEach(n => { try { n.start && n.start(t); } catch { } }); tick(6); },
    fadeTo(v, secs = 2, t = now(), from = null) { const g = output.gain; g.cancelScheduledValues(t); g.setValueAtTime(from ?? g.value, t); g.linearRampToValueAtTime(v, t + secs); },
    hover(t = now()) { if (t - lastHover < 0.12) return; lastHover = t; ping(t, 1760 + rnd() * 220, 1480, 0.55, 0.018, 1.3); },
    select(t = now()) { ping(t, 587.33, 580, 2.4, 0.05); ping(t + 0.14, 880, 874, 2.8, 0.035); ping(t, 196, 194, 3.2, 0.06, 0.8); },
    nova(t = now()) { if (t - lastNova < 4) return; lastNova = t; ping(t, 1318.5, 659.25, 2.2, 0.03, 1.4); },
    bell,
  };
}
