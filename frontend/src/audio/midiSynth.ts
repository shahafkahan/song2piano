import type { InstrumentType, TranscribedNote } from '../types'

/**
 * Self-contained polyphonic Web Audio synthesizer for transcribed MIDI notes.
 * Requires zero external SoundFont downloads and works 100% offline.
 */
export class MidiSynth {
  private ctx: AudioContext | null = null
  private masterGain: GainNode | null = null
  private compressor: DynamicsCompressorNode | null = null
  private notes: TranscribedNote[] = []
  private instrument: InstrumentType = 'piano'
  private volume = 0.75
  private rate = 1.0
  private isPlaying = false
  private currentSongTime = 0
  private nextNoteIndex = 0
  private timerId: number | null = null
  private lastTickPerf = 0
  private activeNodes: Set<{ stop: (when?: number) => void }> = new Set()

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      this.ctx = new AudioCtx()
      this.masterGain = this.ctx.createGain()
      this.masterGain.gain.value = this.volume
      this.compressor = this.ctx.createDynamicsCompressor()
      this.compressor.threshold.setValueAtTime(-16, this.ctx.currentTime)
      this.compressor.knee.setValueAtTime(18, this.ctx.currentTime)
      this.compressor.ratio.setValueAtTime(3.5, this.ctx.currentTime)
      this.compressor.attack.setValueAtTime(0.003, this.ctx.currentTime)
      this.compressor.release.setValueAtTime(0.18, this.ctx.currentTime)
      this.masterGain.connect(this.compressor)
      this.compressor.connect(this.ctx.destination)
    }
    if (this.ctx.state === 'suspended') {
      void this.ctx.resume()
    }
    return this.ctx
  }

  setNotes(notes: TranscribedNote[], instrument: InstrumentType = 'piano'): void {
    this.stop()
    this.notes = [...notes].sort((a, b) => a.start_sec - b.start_sec)
    this.instrument = instrument
  }

  setVolume(vol: number): void {
    this.volume = Math.max(0, Math.min(1, vol))
    if (this.masterGain && this.ctx) {
      this.masterGain.gain.setTargetAtTime(this.volume, this.ctx.currentTime, 0.015)
    }
  }

  setPlaybackRate(rate: number): void {
    this.rate = Math.max(0.25, Math.min(2.0, rate))
  }

  syncToTime(songTimeSec: number, playing: boolean): void {
    if (playing && !this.isPlaying) {
      this.play(songTimeSec)
      return
    }
    if (!playing && this.isPlaying) {
      this.pause(songTimeSec)
      return
    }
    if (Math.abs(songTimeSec - this.currentSongTime) > 0.25) {
      this.seek(songTimeSec)
    }
  }

  play(fromTimeSec?: number): void {
    this.ensureContext()
    if (fromTimeSec !== undefined) {
      this.currentSongTime = Math.max(0, fromTimeSec)
    }
    this.nextNoteIndex = this.findStartIndex(this.currentSongTime)
    this.isPlaying = true
    this.lastTickPerf = performance.now()
    if (this.timerId === null) {
      this.timerId = window.setInterval(() => this.schedulerTick(), 25)
    }
  }

  pause(atTimeSec?: number): void {
    this.isPlaying = false
    if (atTimeSec !== undefined) {
      this.currentSongTime = Math.max(0, atTimeSec)
    }
    if (this.timerId !== null) {
      window.clearInterval(this.timerId)
      this.timerId = null
    }
    this.silenceActive()
  }

  seek(songTimeSec: number): void {
    this.currentSongTime = Math.max(0, songTimeSec)
    this.nextNoteIndex = this.findStartIndex(this.currentSongTime)
    this.lastTickPerf = performance.now()
    this.silenceActive()
  }

  stop(): void {
    this.pause(0)
    this.nextNoteIndex = 0
  }

  dispose(): void {
    this.stop()
    if (this.ctx) {
      void this.ctx.close()
      this.ctx = null
      this.masterGain = null
      this.compressor = null
    }
  }

  private findStartIndex(timeSec: number): number {
    let lo = 0
    let hi = this.notes.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (this.notes[mid].start_sec < timeSec - 0.02) {
        lo = mid + 1
      } else {
        hi = mid
      }
    }
    return lo
  }

  private silenceActive(): void {
    if (!this.ctx) return
    const now = this.ctx.currentTime
    for (const node of this.activeNodes) {
      try {
        node.stop(now + 0.02)
      } catch {
        // already stopped
      }
    }
    this.activeNodes.clear()
  }

  private schedulerTick(): void {
    if (!this.isPlaying || !this.ctx || !this.masterGain) return
    const nowPerf = performance.now()
    const elapsedReal = (nowPerf - this.lastTickPerf) / 1000
    this.lastTickPerf = nowPerf
    this.currentSongTime += elapsedReal * this.rate

    const lookaheadSongSec = 0.14 * this.rate
    const horizon = this.currentSongTime + lookaheadSongSec
    const audioNow = this.ctx.currentTime

    while (
      this.nextNoteIndex < this.notes.length &&
      this.notes[this.nextNoteIndex].start_sec <= horizon
    ) {
      const note = this.notes[this.nextNoteIndex]
      this.nextNoteIndex += 1
      const deltaSong = Math.max(0, note.start_sec - this.currentSongTime)
      const when = audioNow + deltaSong / this.rate
      const dur = Math.max(0.06, (note.end_sec - note.start_sec) / this.rate)
      this.triggerNote(note.midi, note.velocity, when, dur)
    }
  }

  private triggerNote(midi: number, velocity: number, when: number, durationSec: number): void {
    if (!this.ctx || !this.masterGain || this.volume <= 0.005) return

    const ctx = this.ctx
    const freq = 440 * Math.pow(2, (midi - 69) / 12)
    const velNorm = Math.max(0.15, Math.min(1.0, velocity / 127))
    const peakGain = 0.22 * velNorm

    const noteGain = ctx.createGain()
    const filter = ctx.createBiquadFilter()
    filter.type = 'lowpass'
    filter.frequency.setValueAtTime(
      Math.min(12000, freq * (this.instrument === 'bass' ? 3.5 : 6.5) * (0.5 + 0.7 * velNorm)),
      when
    )
    filter.frequency.exponentialRampToValueAtTime(Math.max(300, freq * 2.1), when + Math.min(durationSec, 1.4))

    noteGain.gain.setValueAtTime(0.0001, when)
    noteGain.gain.linearRampToValueAtTime(peakGain, when + (this.instrument === 'piano' ? 0.005 : 0.009))
    noteGain.gain.exponentialRampToValueAtTime(Math.max(0.001, peakGain * 0.35), when + Math.min(durationSec * 0.75, 0.8))
    noteGain.gain.exponentialRampToValueAtTime(0.0001, when + durationSec + 0.08)

    filter.connect(noteGain)
    noteGain.connect(this.masterGain)

    const osc1 = ctx.createOscillator()
    const osc2 = ctx.createOscillator()
    const osc3 = this.instrument === 'piano' ? ctx.createOscillator() : null

    if (this.instrument === 'guitar') {
      osc1.type = 'triangle'
      osc2.type = 'sawtooth'
    } else if (this.instrument === 'strings' || this.instrument === 'vocals') {
      osc1.type = 'sawtooth'
      osc2.type = 'triangle'
    } else if (this.instrument === 'bass') {
      osc1.type = 'triangle'
      osc2.type = 'sine'
    } else {
      // Crisp piano-like harmonics
      osc1.type = 'triangle'
      osc2.type = 'sine'
    }

    osc1.frequency.setValueAtTime(freq, when)
    osc2.frequency.setValueAtTime(freq * 2, when)

    const osc2Gain = ctx.createGain()
    osc2Gain.gain.value = this.instrument === 'piano' ? 0.26 : 0.28
    osc1.connect(filter)
    osc2.connect(osc2Gain)
    osc2Gain.connect(filter)

    const osc3Gain = osc3 ? ctx.createGain() : null
    if (osc3 && osc3Gain) {
      osc3.type = 'sine'
      osc3.frequency.setValueAtTime(freq * 3, when)
      osc3Gain.gain.value = 0.1
      osc3.connect(osc3Gain)
      osc3Gain.connect(filter)
    }

    const stopAt = when + durationSec + 0.1
    osc1.start(when)
    osc2.start(when)
    osc1.stop(stopAt)
    osc2.stop(stopAt)

    this.activeNodes.add(osc1)
    this.activeNodes.add(osc2)
    if (osc3) {
      osc3.start(when)
      osc3.stop(stopAt)
      this.activeNodes.add(osc3)
    }
    osc1.onended = () => {
      this.activeNodes.delete(osc1)
      this.activeNodes.delete(osc2)
      if (osc3) {
        this.activeNodes.delete(osc3)
      }
      noteGain.disconnect()
      filter.disconnect()
    }
  }
}
