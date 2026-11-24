import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Cpu, FileMusic, Layers, Loader2, Radio, Wand2 } from 'lucide-react'
import type { JobDetail, JobEvent } from '../types'

interface ProgressTrackerProps {
  job: JobDetail
  onCompleted: (jobId: string) => void
  onBack: () => void
}

const PIPELINE_STEPS = [
  { key: 'ffmpeg', label: 'FFmpeg Audio Prep', threshold: 10, icon: Radio },
  { key: 'stems', label: 'Demucs Stems', threshold: 20, icon: Layers },
  { key: 'transcribing', label: 'Neural Transcription', threshold: 50, icon: Wand2 },
  { key: 'engraving', label: 'music21 & PDF Engraving', threshold: 80, icon: FileMusic },
  { key: 'completed', label: 'Ready', threshold: 100, icon: CheckCircle2 },
]

export function ProgressTracker({ job, onCompleted, onBack }: ProgressTrackerProps) {
  const [events, setEvents] = useState<JobEvent[]>(job.events || [])
  const [progress, setProgress] = useState<number>(job.progress)
  const [status, setStatus] = useState<string>(job.status)
  const [message, setMessage] = useState<string>(job.message)
  const [device, setDevice] = useState<string | null>(job.compute_device || null)
  const [errorDetail, setErrorDetail] = useState<string | null>(job.error_message || null)

  useEffect(() => {
    setEvents(job.events || [])
    setProgress(job.progress)
    setStatus(job.status)
    setMessage(job.message)
    setDevice(job.compute_device || null)
    setErrorDetail(job.error_message || null)

    if (job.status === 'completed') {
      onCompleted(job.id)
      return
    }
    if (job.status === 'failed') {
      return
    }

    const es = new EventSource(`/api/jobs/${job.id}/events`)

    es.addEventListener('progress', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as JobEvent
        setProgress(data.progress)
        setStatus(data.status)
        setMessage(data.message)
        if (data.device) setDevice(data.device)
        if (data.error_message) setErrorDetail(data.error_message)
        setEvents((prev) => {
          if (prev.some((p) => p.id === data.id && p.message === data.message)) return prev
          return [...prev, data]
        })
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('done', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as { status: string }
        setStatus(data.status)
        es.close()
        if (data.status === 'completed') {
          onCompleted(job.id)
        }
      } catch {
        es.close()
      }
    })

    es.onerror = () => {
      // Allow automatic EventSource reconnect unless terminal
    }

    return () => {
      es.close()
    }
  }, [job.id])

  const isFailed = status === 'failed'

  return (
    <div className="mx-auto max-w-3xl rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-2xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            {isFailed ? (
              <AlertTriangle className="h-5 w-5 text-rose-400" />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-indigo-400" />
            )}
            <h2 className="text-lg font-bold text-white">{job.title}</h2>
          </div>
          <p className="mt-1 text-sm text-slate-300">{message}</p>
        </div>

        <div className="flex items-center gap-2">
          {device && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
              <Cpu className="h-3.5 w-3.5" />
              {device}
            </span>
          )}
          <span className="rounded-full bg-slate-800 px-3 py-1 font-mono text-xs font-bold text-indigo-300">
            {progress}%
          </span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mt-5 h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            isFailed
              ? 'bg-rose-500'
              : 'bg-gradient-to-r from-indigo-500 via-violet-500 to-emerald-400'
          }`}
          style={{ width: `${Math.max(3, progress)}%` }}
        />
      </div>

      {/* Pipeline stages */}
      <div className="mt-6 grid grid-cols-2 gap-2.5 sm:grid-cols-5">
        {PIPELINE_STEPS.map((step) => {
          const Icon = step.icon
          const done = progress >= step.threshold && !isFailed
          const active = !done && progress >= step.threshold - 15 && !isFailed
          return (
            <div
              key={step.key}
              className={`rounded-xl border p-3 text-center transition ${
                done
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : active
                  ? 'border-indigo-500/60 bg-indigo-500/15 text-indigo-200'
                  : 'border-slate-800 bg-slate-950/40 text-slate-500'
              }`}
            >
              <Icon className={`mx-auto h-4 w-4 ${active ? 'animate-pulse' : ''}`} />
              <div className="mt-1.5 text-[11px] font-semibold leading-tight">{step.label}</div>
            </div>
          )
        })}
      </div>

      {/* Live log feed */}
      <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950/80 p-4">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          Live Pipeline Log
        </div>
        <div className="max-h-56 space-y-1.5 overflow-y-auto font-mono text-xs">
          {events.map((ev, idx) => (
            <div key={`${ev.id}-${idx}`} className="flex items-start gap-2.5 text-slate-300">
              <span className="shrink-0 text-slate-500">[{String(ev.progress).padStart(3, ' ')}%]</span>
              <span className="shrink-0 uppercase text-indigo-400">{ev.stage}</span>
              <span className="text-slate-200">{ev.message}</span>
            </div>
          ))}
        </div>
      </div>

      {isFailed && errorDetail && (
        <pre className="mt-4 max-h-48 overflow-auto rounded-xl border border-rose-500/40 bg-rose-950/30 p-3 font-mono text-[11px] text-rose-200">
          {errorDetail}
        </pre>
      )}

      <div className="mt-5 flex justify-end">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-slate-700 px-4 py-2 text-xs font-semibold text-slate-300 transition hover:bg-slate-800"
        >
          Back to Upload Studio
        </button>
      </div>
    </div>
  )
}
