import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  Cpu,
  FileMusic,
  Gauge,
  KeyRound,
  Music2,
  Piano,
  Rows2,
  Sparkles,
  Timer,
} from 'lucide-react'
import { createPianoCover, extractPianoOnly, fetchMusicXml, fetchNotes } from '../api'
import type { JobDetail, NotesPayload } from '../types'
import { AudioMixerBar } from './AudioMixerBar'
import { ExportMenu } from './ExportMenu'
import { SheetMusicView, type SheetMusicHandle } from './SheetMusicView'
import { SynthesiaView, type SynthesiaHandle } from './SynthesiaView'

type ViewMode = 'sheet' | 'synthesia' | 'split'

interface ScoreWorkspaceProps {
  job: JobDetail
  onBack: () => void
  onJobCreated?: (newJob: JobDetail) => void
}

export function ScoreWorkspace({ job, onBack, onJobCreated }: ScoreWorkspaceProps) {
  const sheetRef = useRef<SheetMusicHandle | null>(null)
  const synthesiaRef = useRef<SynthesiaHandle | null>(null)

  const [viewMode, setViewMode] = useState<ViewMode>('sheet')
  const [musicXml, setMusicXml] = useState<string>('')
  const [notesData, setNotesData] = useState<NotesPayload | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [extractingPiano, setExtractingPiano] = useState<boolean>(false)
  const [creatingCover, setCreatingCover] = useState<boolean>(false)
  const [currentTimeSec, setCurrentTimeSec] = useState<number>(0)
  const [isPlaying, setIsPlaying] = useState<boolean>(false)
  const [seekTarget, setSeekTarget] = useState<{ time: number; seq: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoadError(null)
    setCurrentTimeSec(0)
    setIsPlaying(false)

    const load = async () => {
      try {
        const [xml, notes] = await Promise.all([
          job.artifacts.musicxml ? fetchMusicXml(job.artifacts.musicxml) : Promise.resolve(''),
          job.artifacts.notes ? fetchNotes(job.artifacts.notes) : Promise.resolve(null),
        ])
        if (!cancelled) {
          setMusicXml(xml)
          setNotesData(notes)
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : 'Failed to load score artifacts.')
        }
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [job.id, job.artifacts.musicxml, job.artifacts.notes])

  const handleHighFreqTime = useCallback((t: number) => {
    sheetRef.current?.updateTime(t)
    synthesiaRef.current?.updateTime(t)
  }, [])

  const handleSeek = useCallback((t: number) => {
    setSeekTarget((prev) => ({ time: t, seq: (prev?.seq ?? 0) + 1 }))
  }, [])

  const handleExtractPianoOnly = async () => {
    if (extractingPiano) return
    setExtractingPiano(true)
    setLoadError(null)
    try {
      const created = await extractPianoOnly(job.id, job.quantization || 'auto')
      onJobCreated?.(created)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to start piano-only extraction.')
    } finally {
      setExtractingPiano(false)
    }
  }

  const handleCreatePianoCover = async () => {
    if (creatingCover) return
    setCreatingCover(true)
    setLoadError(null)
    try {
      const created = await createPianoCover(job.id, job.quantization || 'auto', 'auto')
      onJobCreated?.(created)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to start full-song piano cover.')
    } finally {
      setCreatingCover(false)
    }
  }

  const bpm = notesData?.bpm || job.detected_bpm || 120
  const isPianoOnly = job.stem_mode === 'piano_only'
  const isPianoCover = job.stem_mode === 'piano_cover'
  const notes = notesData?.notes || []

  return (
    <div className="space-y-4">
      {/* Top metadata & export bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-800 bg-slate-900/80 px-5 py-4 shadow-xl">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg border border-slate-700 bg-slate-950 p-2 text-slate-300 transition hover:bg-slate-800"
            title="Back to Studio"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-bold text-white">{job.title}</h1>
              {isPianoOnly && (
                <span className="rounded-full border border-indigo-400/40 bg-indigo-500/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                  Piano Only Isolated
                </span>
              )}
              {isPianoCover && (
                <span className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-300">
                  Full Song Piano Cover
                </span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <span className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-2 py-0.5 capitalize text-slate-200">
                <Music2 className="h-3 w-3 text-indigo-400" />
                {job.instrument}
              </span>
              <span className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-2 py-0.5 text-slate-200">
                <Gauge className="h-3 w-3 text-emerald-400" />
                {Math.round(bpm)} BPM
              </span>
              {job.detected_key && (
                <span className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-2 py-0.5 text-slate-200">
                  <KeyRound className="h-3 w-3 text-amber-400" />
                  {job.detected_key}
                </span>
              )}
              <span className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-2 py-0.5 text-slate-200">
                <Timer className="h-3 w-3 text-sky-400" />
                {job.time_signature} · {notesData?.quantization || job.quantization}
              </span>
              {job.note_count != null && (
                <span className="text-slate-400">
                  {job.note_count} notes · {job.measure_count ?? '?'} bars
                </span>
              )}
              {job.compute_device && (
                <span className="hidden items-center gap-1 text-slate-400 md:inline-flex">
                  <Cpu className="h-3 w-3" />
                  {job.compute_device}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {!isPianoCover && (
            <button
              type="button"
              onClick={handleCreatePianoCover}
              disabled={creatingCover}
              className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3.5 py-2 text-xs font-semibold text-amber-200 transition hover:bg-amber-500/20 disabled:opacity-50"
              title="Convert the entire song (singing melody + accompaniment + bass) into a playable solo piano arrangement"
            >
              <Sparkles className="h-3.5 w-3.5 text-amber-400" />
              {creatingCover ? 'Starting Piano Cover...' : 'Full Song → Piano Cover'}
            </button>
          )}
          {!isPianoOnly && (
            <button
              type="button"
              onClick={handleExtractPianoOnly}
              disabled={extractingPiano}
              className="inline-flex items-center gap-1.5 rounded-xl border border-indigo-500/40 bg-indigo-500/10 px-3.5 py-2 text-xs font-semibold text-indigo-200 transition hover:bg-indigo-500/20 disabled:opacity-50"
              title="Re-run with 6-stem Demucs to isolate only the piano track and regenerate piano-only sheets"
            >
              <Piano className="h-3.5 w-3.5 text-indigo-400" />
              {extractingPiano ? 'Starting Piano Extraction...' : 'Extract Piano Only'}
            </button>
          )}
          <ExportMenu artifacts={job.artifacts} />
        </div>
      </div>

      {loadError ? (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-6 text-sm text-rose-200">
          {loadError}
        </div>
      ) : (
        <>
          <AudioMixerBar
            artifacts={job.artifacts}
            notes={notes}
            instrument={job.instrument}
            stemMode={job.stem_mode}
            currentTimeSec={currentTimeSec}
            isPlaying={isPlaying}
            seekTargetSec={seekTarget}
            onTimeUpdate={setCurrentTimeSec}
            onHighFreqTime={handleHighFreqTime}
            onPlayingChange={setIsPlaying}
          />

          {/* View Mode Switcher */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="inline-flex items-center rounded-xl border border-slate-800 bg-slate-900/90 p-1 shadow-lg">
              {(
                [
                  ['sheet', 'Sheet Music', FileMusic],
                  ['synthesia', 'Synthesia', Piano],
                  ['split', 'Split View', Rows2],
                ] as const
              ).map(([mode, label, Icon]) => {
                const active = viewMode === mode
                return (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setViewMode(mode)}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                      active
                        ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-500/30'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </button>
                )
              })}
            </div>
          </div>

          {(viewMode === 'synthesia' || viewMode === 'split') && (
            <SynthesiaView
              ref={synthesiaRef}
              notes={notes}
              bpm={bpm}
              timeSignature={job.time_signature}
              beatTimes={notesData?.beat_times}
              beatIndices={notesData?.beat_indices}
              currentTimeSec={currentTimeSec}
              compact={viewMode === 'split'}
              onSeek={handleSeek}
            />
          )}

          {(viewMode === 'sheet' || viewMode === 'split') &&
            (musicXml ? (
              <SheetMusicView
                ref={sheetRef}
                musicXml={musicXml}
                bpm={bpm}
                instrument={job.instrument}
                notes={notes}
                beatTimes={notesData?.beat_times}
                beatIndices={notesData?.beat_indices}
                currentTimeSec={currentTimeSec}
                onSeek={handleSeek}
              />
            ) : (
              <div className="rounded-2xl border border-slate-800 bg-slate-900 p-12 text-center text-sm text-slate-400">
                Loading sheet music...
              </div>
            ))}
        </>
      )}
    </div>
  )
}
