import { useEffect, useMemo, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import { Pause, Play, RotateCcw, Repeat, Volume2, Music } from 'lucide-react'
import { MidiSynth } from '../audio/midiSynth'
import type { ArtifactUrls, InstrumentType, StemModeType, TranscribedNote } from '../types'

interface AudioMixerBarProps {
  artifacts: ArtifactUrls
  notes: TranscribedNote[]
  instrument: InstrumentType
  stemMode?: StemModeType
  currentTimeSec: number
  isPlaying: boolean
  seekTargetSec?: { time: number; seq: number } | null
  onTimeUpdate: (timeSec: number) => void
  onHighFreqTime?: (timeSec: number) => void
  onPlayingChange: (playing: boolean) => void
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const m = Math.floor(s / 60)
  const rem = s % 60
  return `${m}:${String(rem).padStart(2, '0')}`
}

export function AudioMixerBar({
  artifacts,
  notes,
  instrument,
  stemMode,
  currentTimeSec,
  isPlaying,
  seekTargetSec,
  onTimeUpdate,
  onHighFreqTime,
  onPlayingChange,
}: AudioMixerBarProps) {
  const waveRef = useRef<HTMLDivElement | null>(null)
  const wsRef = useRef<WaveSurfer | null>(null)
  const synthRef = useRef<MidiSynth | null>(null)

  const defaultSource =
    stemMode === 'piano_cover'
      ? 'mix'
      : artifacts.stems?.piano && (stemMode === 'piano_only' || instrument === 'piano')
      ? 'piano'
      : 'mix'

  const [selectedSource, setSelectedSource] = useState<string>(defaultSource)
  const [duration, setDuration] = useState<number>(0)
  const [audioVol, setAudioVol] = useState<number>(0.85)
  const [midiVol, setMidiVol] = useState<number>(0.55)
  const [speed, setSpeed] = useState<number>(1.0)
  const [loop, setLoop] = useState<boolean>(false)

  const loopRef = useRef(loop)
  loopRef.current = loop
  const highFreqRef = useRef(onHighFreqTime)
  highFreqRef.current = onHighFreqTime
  const pendingRestoreRef = useRef<{ time: number; play: boolean } | null>(null)

  useEffect(() => {
    setSelectedSource(defaultSource)
  }, [defaultSource])

  const sourceOptions = useMemo(() => {
    const opts: Array<{ id: string; label: string; url: string }> = []
    if (artifacts.audio) {
      opts.push({ id: 'mix', label: 'Original Mix', url: artifacts.audio })
    }
    const stemOrder = ['piano', 'guitar', 'vocals', 'bass', 'drums', 'other']
    for (const s of stemOrder) {
      if (artifacts.stems?.[s]) {
        const label = s === 'piano' ? 'Piano (Isolated)' : s[0].toUpperCase() + s.slice(1)
        opts.push({ id: s, label, url: artifacts.stems[s] })
      }
    }
    return opts
  }, [artifacts])

  const activeUrl =
    sourceOptions.find((o) => o.id === selectedSource)?.url || sourceOptions[0]?.url || ''

  // Initialize Web Audio MIDI Synth
  useEffect(() => {
    const synth = new MidiSynth()
    synth.setNotes(notes, instrument)
    synth.setVolume(midiVol)
    synth.setPlaybackRate(speed)
    synthRef.current = synth

    return () => {
      synth.dispose()
      synthRef.current = null
    }
  }, [notes, instrument])

  useEffect(() => {
    synthRef.current?.setVolume(midiVol)
  }, [midiVol])

  useEffect(() => {
    synthRef.current?.setPlaybackRate(speed)
    wsRef.current?.setPlaybackRate(speed, true)
  }, [speed])

  useEffect(() => {
    wsRef.current?.setVolume(audioVol)
  }, [audioVol])

  // External seek (from clicking on sheet music)
  useEffect(() => {
    if (!seekTargetSec) return
    const ws = wsRef.current
    if (!ws) return
    const clamped = Math.max(0, Math.min(seekTargetSec.time, ws.getDuration() || seekTargetSec.time))
    ws.setTime(clamped)
    synthRef.current?.seek(clamped)
    onTimeUpdate(clamped)
    highFreqRef.current?.(clamped)
  }, [seekTargetSec])

  // 60 FPS requestAnimationFrame playhead driver while playing
  useEffect(() => {
    if (!isPlaying) return
    let rafId = 0
    const tick = () => {
      const ws = wsRef.current
      if (ws && ws.isPlaying()) {
        highFreqRef.current?.(ws.getCurrentTime())
      }
      rafId = window.requestAnimationFrame(tick)
    }
    rafId = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(rafId)
  }, [isPlaying])

  // Initialize WaveSurfer
  useEffect(() => {
    const container = waveRef.current
    if (!container || !activeUrl) return

    const ws = WaveSurfer.create({
      container,
      waveColor: '#475569',
      progressColor: '#6366f1',
      cursorColor: '#a5b4fc',
      cursorWidth: 2,
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      height: 54,
      normalize: true,
    })

    wsRef.current = ws
    ws.setVolume(audioVol)
    ws.load(activeUrl)

    ws.on('ready', () => {
      setDuration(ws.getDuration())
      ws.setPlaybackRate(speed, true)
      if (pendingRestoreRef.current) {
        const { time, play } = pendingRestoreRef.current
        pendingRestoreRef.current = null
        ws.setTime(Math.min(time, ws.getDuration()))
        if (play) {
          void ws.play()
        }
      }
    })

    ws.on('play', () => {
      onPlayingChange(true)
      synthRef.current?.play(ws.getCurrentTime())
    })

    ws.on('pause', () => {
      onPlayingChange(false)
      synthRef.current?.pause(ws.getCurrentTime())
      highFreqRef.current?.(ws.getCurrentTime())
    })

    ws.on('timeupdate', (t) => {
      onTimeUpdate(t)
      highFreqRef.current?.(t)
      synthRef.current?.syncToTime(t, ws.isPlaying())
    })

    ws.on('seeking', (t) => {
      onTimeUpdate(t)
      highFreqRef.current?.(t)
      synthRef.current?.seek(t)
    })

    ws.on('finish', () => {
      if (loopRef.current) {
        ws.setTime(0)
        synthRef.current?.seek(0)
        highFreqRef.current?.(0)
        void ws.play()
      } else {
        onPlayingChange(false)
        synthRef.current?.pause(0)
      }
    })

    return () => {
      ws.destroy()
      wsRef.current = null
    }
  }, [activeUrl])

  const handleSourceSwitch = (nextId: string) => {
    if (nextId === selectedSource) return
    const ws = wsRef.current
    pendingRestoreRef.current = {
      time: ws ? ws.getCurrentTime() : currentTimeSec,
      play: ws ? ws.isPlaying() : isPlaying,
    }
    setSelectedSource(nextId)
  }

  const togglePlay = () => {
    const ws = wsRef.current
    if (!ws) return
    if (ws.isPlaying()) {
      ws.pause()
    } else {
      void ws.play()
    }
  }

  const restart = () => {
    const ws = wsRef.current
    if (!ws) return
    ws.setTime(0)
    onTimeUpdate(0)
    highFreqRef.current?.(0)
    synthRef.current?.seek(0)
  }

  // Keyboard shortcuts: Space = play/pause, R = restart
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.code === 'Space') {
        e.preventDefault()
        togglePlay()
      } else if (e.key === 'r' || e.key === 'R') {
        restart()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-4 shadow-xl">
      {/* Stem selector */}
      {sourceOptions.length > 1 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Audio Track:
          </span>
          {sourceOptions.map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => handleSourceSwitch(opt.id)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                selectedSource === opt.id
                  ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-500/30'
                  : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}

      {/* Waveform */}
      <div className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
        <div ref={waveRef} />
      </div>

      {/* Transport + Mixers */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={togglePlay}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg shadow-indigo-500/25 transition hover:bg-indigo-500"
            title="Play / Pause (Space)"
          >
            {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
          </button>
          <button
            type="button"
            onClick={restart}
            className="rounded-lg border border-slate-700 bg-slate-800/80 p-2 text-slate-300 transition hover:bg-slate-700"
            title="Restart (R)"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setLoop((v) => !v)}
            className={`rounded-lg border p-2 transition ${
              loop
                ? 'border-indigo-500 bg-indigo-500/20 text-indigo-300'
                : 'border-slate-700 bg-slate-800/80 text-slate-400 hover:bg-slate-700'
            }`}
            title="Loop playback"
          >
            <Repeat className="h-4 w-4" />
          </button>
          <span className="ml-2 font-mono text-xs text-slate-300">
            {formatTime(currentTimeSec)} / {formatTime(duration)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/60 p-1 text-[11px]">
            <button
              type="button"
              onClick={() => {
                setAudioVol(0.85)
                setMidiVol(0.55)
              }}
              className="rounded px-2 py-1 text-slate-300 hover:bg-slate-800"
            >
              Mix
            </button>
            <button
              type="button"
              onClick={() => {
                setAudioVol(1.0)
                setMidiVol(0.0)
              }}
              className="rounded px-2 py-1 text-slate-300 hover:bg-slate-800"
            >
              Audio Only
            </button>
            <button
              type="button"
              onClick={() => {
                setAudioVol(0.0)
                setMidiVol(0.85)
              }}
              className="rounded px-2 py-1 text-slate-300 hover:bg-slate-800"
            >
              MIDI Only
            </button>
          </div>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <Volume2 className="h-3.5 w-3.5 text-slate-400" />
            <span>Audio</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={audioVol}
              onChange={(e) => setAudioVol(Number(e.target.value))}
              className="w-20 accent-indigo-500"
            />
          </label>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <Music className="h-3.5 w-3.5 text-indigo-400" />
            <span>MIDI Synth</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={midiVol}
              onChange={(e) => setMidiVol(Number(e.target.value))}
              className="w-20 accent-violet-500"
            />
          </label>

          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
            title="Playback Speed"
          >
            <option value={0.5}>0.50x</option>
            <option value={0.75}>0.75x</option>
            <option value={1.0}>1.00x</option>
            <option value={1.25}>1.25x</option>
            <option value={1.5}>1.50x</option>
          </select>
        </div>
      </div>
    </div>
  )
}
