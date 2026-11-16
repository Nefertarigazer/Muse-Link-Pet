// sound.js - the confirmation chime ("received"), synthesized, no audio file.
// Bright two-note rise, short and clean (Apple Pay-like feel); deliberately
// unlike muse-chime.wav, which means "come here". The backend renders the
// same parameters to pet-confirm.wav for popup.ps1 (contract.json
// "confirm_chime"), so both ends sound identical.
export const CHIME = {
  notes: [{ hz: 1318.5, at: 0 }, { hz: 1975.5, at: 0.085 }],
  attack: 0.004, decay: 0.42, gain: 0.22, overtone: 0.18,
};

let ctx = null;

export function playConfirm() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
    const t0 = ctx.currentTime + 0.01;
    for (const n of CHIME.notes) {
      for (const [mult, level] of [[1, 1], [2, CHIME.overtone]]) {
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = n.hz * mult;
        const start = t0 + n.at;
        g.gain.setValueAtTime(0, start);
        g.gain.linearRampToValueAtTime(CHIME.gain * level, start + CHIME.attack);
        g.gain.exponentialRampToValueAtTime(0.0001, start + CHIME.decay);
        osc.connect(g).connect(ctx.destination);
        osc.start(start);
        osc.stop(start + CHIME.decay + 0.05);
      }
    }
  } catch (e) { /* no audio device: the check mark still shows */ }
}
