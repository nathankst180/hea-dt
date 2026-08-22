// A small, direct O(n^2) Discrete Fourier Transform — deliberately not a
// full FFT library dependency, since the window we run this over (a few
// dozen recent accelerometer samples) is tiny. Used by PredictionsPanel
// to show a REAL vibration spectrum computed from replayed accel_xyz
// data, rather than a fabricated placeholder — M5's own spectral
// analysis is still a Phase 2 stub, but the underlying signal is real.

export interface SpectrumBin {
  freqHz: number;
  magnitude: number;
}

export function computeMagnitudeSpectrum(samples: number[], sampleRateHz: number): SpectrumBin[] {
  const n = samples.length;
  if (n < 4 || !Number.isFinite(sampleRateHz) || sampleRateHz <= 0) return [];

  const mean = samples.reduce((a, b) => a + b, 0) / n;
  const centered = samples.map((s) => s - mean);
  const usableBins = Math.floor(n / 2);

  const bins: SpectrumBin[] = [];
  for (let k = 1; k <= usableBins; k++) {
    let re = 0;
    let im = 0;
    for (let t = 0; t < n; t++) {
      const angle = (-2 * Math.PI * k * t) / n;
      re += centered[t] * Math.cos(angle);
      im += centered[t] * Math.sin(angle);
    }
    bins.push({
      freqHz: (k * sampleRateHz) / n,
      magnitude: Math.sqrt(re * re + im * im) / n,
    });
  }
  return bins;
}
