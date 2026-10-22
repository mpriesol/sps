// Short, distinct tones with an envelope to avoid clicks at the start/end.
const patterns = {
  match: [[1320, 0, .17]],
  duplicate: [[1046, 0, .09], [1046, .16, .09]],
  unknown: [[220, 0, .12], [220, .19, .12], [220, .38, .15]],
};

export async function playScanSound(audio, kind) {
  try {
    if (!audio) return;
    if (audio.state !== 'running') await audio.resume();
    const negative = kind === 'unknown' || kind === 'full';
    const notes = patterns[kind] || patterns.unknown;
    const start = audio.currentTime + .01;
    for (const [frequency, offset, duration] of notes) {
      const oscillator = audio.createOscillator(), gain = audio.createGain();
      oscillator.type = negative ? 'sawtooth' : 'triangle';
      oscillator.frequency.value = frequency;
      oscillator.connect(gain); gain.connect(audio.destination);
      const t = start + offset;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(negative ? .28 : .5, t + .008);
      gain.gain.setValueAtTime(negative ? .28 : .5, t + duration - .025);
      gain.gain.linearRampToValueAtTime(0, t + duration);
      oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      oscillator.start(t); oscillator.stop(t + duration);
    }
  } catch { /* Scanning remains available when the device blocks audio. */ }
}
