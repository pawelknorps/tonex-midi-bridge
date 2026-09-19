// gen_vectors.js — byte-exact ground truth from tonex.js (the working WebSerial editor).
// Usage: node scripts/gen_vectors.js  (prints a JSON blob consumed by test_proto.py)
const fs = require('node:fs');
const vm = require('node:vm');

const src = fs.readFileSync(__dirname + '/../../tonex-one-control/tonex.js', 'utf8');
const ctx = vm.createContext({
  navigator: {}, console,
  TextDecoder: globalThis.TextDecoder, Uint8Array: globalThis.Uint8Array,
  Float64Array: globalThis.Float64Array, ArrayBuffer: globalThis.ArrayBuffer,
  DataView: globalThis.DataView, setInterval: globalThis.setInterval,
  clearInterval: globalThis.clearInterval, setTimeout: globalThis.setTimeout,
});
vm.runInContext(src, ctx);
const run = (expr) => vm.runInContext(`(()=>{${expr}})()`, ctx);
const hx = (a) => Array.from(a).map((b) => b.toString(16).padStart(2, '0')).join(' ');

const f32 = (v) => { const b = new ArrayBuffer(4); new DataView(b).setFloat32(0, v, true); return new Uint8Array(b); };
const mkState = () => {
  const sd = new Uint8Array(40);
  for (let i = 0; i < 40; i++) sd[i] = (i * 7 + 3) & 0xFF; // deterministic pseudo pattern
  sd[22] = 2; sd[24] = 7; sd[26] = 0;      // slotA=2, slotB=7, slotC=0  (tail offsets 18/16/14)
  sd[29] = 0; sd[28] = 0; sd[33] = 0;      // currentSlot=0(A), bypass=0, dmon=0
  sd[34] = 1;                              // tempo source = global
  sd[31] = 0xB8; sd[32] = 0x01;            // tuneref = 440 u16 LE
  sd.set(f32(120.0), 36);                  // BPM
  sd.set(f32(0.5), 15);                    // trim
  sd[20] = 1;                              // cabsim
  return sd;
};
const withPad = (t, cur, slotA, slotB, slotC, active, bypass, n) => {
  t.stateData = mkState();
  t.currentSlot = cur; t.slotA = slotA; t.slotB = slotB; t.slotC = slotC;
  t.currentPreset = active; t.bypassMode = bypass;
  t._doSetPreset(n);
  return t.stateData;
};

const out = {
  crc_010203: run('return _crc(new Uint8Array([0x01, 0x02, 0x03]))'),
  hello: hx(run('return _hello()')),
  req_state: hx(run('return _reqState()')),
  req_mvol: hx(run('return _reqMvol()')),
  req_preset_4: hx(run('return _reqPreset(4, false)')),
  req_preset_4_full: hx(run('return _reqPreset(4, true)')),
  send_param_20_5: hx(run('return _sendParam(20, 5.0)')),
  send_param_42_5_5: hx(run('return _sendParam(42, 5.5)')),
  send_mvol_5: hx(run('return _sendMvol(5.0)')),
  frame_esc: hx(run('return _frame(new Uint8Array([0xAA, 0x7E, 0x7D, 0x00]))')),
  state: hx(mkState()),
  set_state_frame: hx(run('return _setState(' + JSON.stringify(Array.from(mkState())) + ')')),
  // _doSetPreset case 1: slot A, preset 2 -> 5
  patch_slotA_2to5: hx(withPad(new (vm.runInContext('TonexSerial', ctx))(() => {}), 0, 2, 7, 0, 2, 0, 5)),
  // case 2: slot B active (7) -> 5
  patch_slotB_7to5: hx(withPad(new (vm.runInContext('TonexSerial', ctx))(() => {}), 1, 2, 7, 0, 7, 0, 5)),
  // case 3: same preset 2 re-selected -> JS toggles bypass 0->1
  patch_repeat_toggle: hx(withPad(new (vm.runInContext('TonexSerial', ctx))(() => {}), 0, 2, 7, 0, 2, 0, 2)),
};
console.log(JSON.stringify(out, null, 1));