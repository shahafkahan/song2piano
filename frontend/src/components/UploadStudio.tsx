import { useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  FileAudio,
  Guitar,
  Layers,
  Mic2,
  Music,
  Piano,
  Sliders,
  Sparkles,
  UploadCloud,
  Waves,
} from 'lucide-react'
import type { CreateJobParams, InstrumentType, QuantizationType, StemModeType } from '../types'

interface UploadStudioProps {
  onSubmit: (params: CreateJobParams) => Promise<void>
  isSubmitting: boolean
  error: string | null
}

const INSTRUMENTS: Array<{
  id: InstrumentType
  label: string
  engine: string
  icon: typeof Piano
}> = [
  { id: 'piano', label: 'Piano', engine: 'ByteDance CRNN', icon: Piano },
  { id: 'guitar', label: 'Guitar', engine: 'Basic Pitch ONNX', icon: Guitar },
  { id: 'vocals', label: 'Vocals', engine: 'Basic Pitch ONNX', icon: Mic2 },
  { id: 'bass', label: 'Bass', engine: 'Basic Pitch ONNX', icon: Waves },
  { id: 'strings', label: 'Strings', engine: 'Basic Pitch ONNX', icon: Music },
  { id: 'other', label: 'Other / Mix', engine: 'Basic Pitch ONNX', icon: Sparkles },
]

const STEM_MODES: Array<{
  id: StemModeType
  title: string
  badge: string
  desc: string
}> = [
  {
    id: 'piano_cover',
    title: 'Full Song → Piano Cover',
    badge: 'Vocals + Harmony',
    desc: 'Converts the entire song — singing melody, chords & bass — into a playable solo piano grand-staff arrangement.',
  },
  {
    id: 'piano_only',
    title: 'Extract Piano Only',
    badge: 'Smart Isolation',
    desc: 'Isolates only the piano stem (removes vocals, drums, bass, guitar & strings) and generates piano-only sheets.',
  },
  {
    id: 'auto',
    title: 'Remove Vocals & Rhythm',
    badge: '4-Stem Clean',
    desc: 'Flagship htdemucs separation; best for ballads, vocals, or bass.',
  },
  {
    id: 'none',
    title: 'Full Mix (No Separation)',
    badge: 'Direct',
    desc: 'Skips stem separation for already-solo piano or clean recordings.',
  },
]

const QUANTIZATIONS: Array<{ value: QuantizationType; label: string }> = [
  { value: 'auto', label: 'Auto' },
  { value: '1/4', label: '1/4' },
  { value: '1/8', label: '1/8' },
  { value: '1/16', label: '1/16' },
  { value: '1/32', label: '1/32' },
  { value: 'triplet', label: 'Triplet' },
]

export function UploadStudio({ onSubmit, isSubmitting, error }: UploadStudioProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [instrument, setInstrument] = useState<InstrumentType>('piano')
  const [stemMode, setStemMode] = useState<StemModeType>('piano_only')
  const [quantization, setQuantization] = useState<QuantizationType>('auto')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [bpm, setBpm] = useState('')
  const [timeSignature, setTimeSignature] = useState('auto')
  const [keySignature, setKeySignature] = useState('')
  const [loadingSample, setLoadingSample] = useState(false)

  const handleFilePick = (picked: File | null) => {
    if (!picked) return
    setFile(picked)
    if (!title) {
      setTitle(picked.name.replace(/\.[^/.]+$/, ''))
    }
  }

  const handleStemModeChange = (mode: StemModeType) => {
    setStemMode(mode)
    if (mode === 'piano_only' || mode === 'piano_cover') {
      setInstrument('piano')
    }
  }

  const handleInstrumentChange = (nextInst: InstrumentType) => {
    setInstrument(nextInst)
    if (nextInst !== 'piano' && (stemMode === 'piano_only' || stemMode === 'piano_cover')) {
      setStemMode('auto')
    } else if (nextInst === 'piano' && stemMode === 'auto') {
      setStemMode('piano_only')
    }
  }

  const handleLoadDemo = async () => {
    setLoadingSample(true)
    try {
      const res = await fetch('/samples/demo-piano.wav')
      if (!res.ok) throw new Error('Demo sample could not be loaded.')
      const blob = await res.blob()
      const demoFile = new File([blob], 'demo-piano.wav', { type: 'audio/wav' })
      setFile(demoFile)
      setTitle('C Major Prelude (Demo)')
      setInstrument('piano')
      setStemMode('auto')
      setQuantization('auto')
    } finally {
      setLoadingSample(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file || isSubmitting) return
    await onSubmit({
      file,
      title: title.trim() || undefined,
      instrument: stemMode === 'piano_only' || stemMode === 'piano_cover' ? 'piano' : instrument,
      separate_stems: stemMode !== 'none',
      stem_mode: stemMode,
      quantization,
      bpm: bpm.trim() ? Number(bpm) : null,
      time_signature: timeSignature,
      key_signature: keySignature.trim() || undefined,
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/40 backdrop-blur"
    >
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Transcribe Audio to Sheet Music</h1>
          <p className="mt-1 text-sm text-slate-400">
            Zero paid APIs. Runs FFmpeg, Demucs stem isolation, ByteDance / Basic Pitch ONNX, and
            music21 + Verovio engraving.
          </p>
        </div>
        <button
          type="button"
          onClick={handleLoadDemo}
          disabled={loadingSample || isSubmitting}
          className="inline-flex items-center gap-2 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3.5 py-2 text-xs font-semibold text-indigo-300 transition hover:bg-indigo-500/20 disabled:opacity-50"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {loadingSample ? 'Loading Demo...' : 'Load Demo Piano Sample'}
        </button>
      </div>

      {/* Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragActive(false)
          handleFilePick(e.dataTransfer.files?.[0] ?? null)
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-6 text-center transition ${
          dragActive
            ? 'border-indigo-400 bg-indigo-500/10'
            : file
            ? 'border-emerald-500/50 bg-emerald-500/5'
            : 'border-slate-700 bg-slate-950/50 hover:border-slate-500'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".mp3,.wav,.flac,.m4a,.ogg,.aac,.webm,audio/*"
          className="hidden"
          onChange={(e) => handleFilePick(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <div className="flex flex-col items-center gap-2">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
              <FileAudio className="h-6 w-6" />
            </div>
            <p className="text-sm font-semibold text-white">{file.name}</p>
            <p className="text-xs text-slate-400">
              {(file.size / (1024 * 1024)).toFixed(2)} MB · Click or drop to replace
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-slate-800 text-indigo-400">
              <UploadCloud className="h-6 w-6" />
            </div>
            <p className="text-sm font-medium text-slate-200">
              Drop an audio file here, or <span className="text-indigo-400 underline">browse</span>
            </p>
            <p className="text-xs text-slate-500">MP3, WAV, FLAC, M4A, OGG, AAC, WEBM (up to 100 MB)</p>
          </div>
        )}
      </div>

      {/* Title */}
      <div className="mt-5">
        <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-slate-400">
          Score Title
        </label>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Nocturne in C Major"
          className="w-full rounded-lg border border-slate-700 bg-slate-950/70 px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none"
        />
      </div>

      {/* Extraction Mode */}
      <div className="mt-5">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          <Layers className="h-3.5 w-3.5 text-indigo-400" />
          Audio Extraction Mode
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {STEM_MODES.map((mode) => {
            const active = stemMode === mode.id
            return (
              <button
                key={mode.id}
                type="button"
                onClick={() => handleStemModeChange(mode.id)}
                className={`rounded-xl border p-3.5 text-left transition ${
                  active
                    ? 'border-indigo-500 bg-indigo-500/15 ring-1 ring-indigo-500/50'
                    : 'border-slate-800 bg-slate-950/50 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-white">{mode.title}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      active ? 'bg-indigo-500/30 text-indigo-200' : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {mode.badge}
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">{mode.desc}</p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Target Instrument */}
      <div className="mt-5">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-slate-400">
          Target Instrument & Transcription Model
        </label>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {INSTRUMENTS.map((inst) => {
            const Icon = inst.icon
            const selected = instrument === inst.id
            return (
              <button
                key={inst.id}
                type="button"
                onClick={() => handleInstrumentChange(inst.id)}
                className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                  selected
                    ? 'border-indigo-500 bg-indigo-500/15 ring-1 ring-indigo-500/50'
                    : 'border-slate-800 bg-slate-950/50 hover:border-slate-700'
                }`}
              >
                <div
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
                    selected ? 'bg-indigo-500 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-white">{inst.label}</div>
                  <div className="truncate text-[11px] text-slate-400">{inst.engine}</div>
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Quantization */}
      <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-3.5">
        <div className="mb-2 flex items-center gap-2">
          <Sliders className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-semibold text-white">Quantization Grid</span>
        </div>
        <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
          {QUANTIZATIONS.map((q) => (
            <button
              key={q.value}
              type="button"
              onClick={() => setQuantization(q.value)}
              className={`rounded-md py-1.5 text-xs font-semibold transition ${
                quantization === q.value
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-800/80 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {q.label}
            </button>
          ))}
        </div>
      </div>

      {/* Advanced options */}
      <div className="mt-4">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-200"
        >
          {showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          Advanced Engraving Overrides (BPM, Time & Key Signature)
        </button>

        {showAdvanced && (
          <div className="mt-3 grid gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-4 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs text-slate-400">BPM (auto if blank)</label>
              <input
                type="number"
                min={30}
                max={260}
                step={0.5}
                value={bpm}
                onChange={(e) => setBpm(e.target.value)}
                placeholder="Auto-detect"
                className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-400">Time Signature</label>
              <select
                value={timeSignature}
                onChange={(e) => setTimeSignature(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white focus:border-indigo-500 focus:outline-none"
              >
                <option value="auto">Auto-detect (3/4 or 4/4)</option>
                <option value="4/4">4/4</option>
                <option value="3/4">3/4</option>
                <option value="2/4">2/4</option>
                <option value="6/8">6/8</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-slate-400">Key (auto if blank)</label>
              <input
                type="text"
                value={keySignature}
                onChange={(e) => setKeySignature(e.target.value)}
                placeholder="e.g. C major, A minor"
                className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2.5 text-xs text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-6 flex justify-end">
        <button
          type="submit"
          disabled={!file || isSubmitting}
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 transition hover:from-indigo-500 hover:to-violet-500 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
        >
          <Sparkles className="h-4 w-4" />
          {isSubmitting
            ? 'Uploading & Queuing...'
            : stemMode === 'piano_cover'
            ? 'Convert Full Song to Piano Sheets'
            : stemMode === 'piano_only'
            ? 'Extract Piano & Generate Sheets'
            : 'Transcribe to Sheet Music'}
        </button>
      </div>
    </form>
  )
}
