import { AlertCircle, CheckCircle2, Clock, Loader2, Music, Trash2 } from 'lucide-react'
import type { JobSummary } from '../types'

interface JobHistoryProps {
  jobs: JobSummary[]
  activeJobId: string | null
  onSelect: (jobId: string) => void
  onDelete: (jobId: string) => void
}

export function JobHistory({ jobs, activeJobId, onSelect, onDelete }: JobHistoryProps) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 shadow-xl">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider text-slate-300">
          Recent Transcriptions
        </h2>
        <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs font-medium text-slate-400">
          {jobs.length}
        </span>
      </div>

      {jobs.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-800 py-10 text-center">
          <Music className="mx-auto h-8 w-8 text-slate-600" />
          <p className="mt-2 text-xs text-slate-400">
            No transcriptions yet. Upload audio or load the demo piano sample.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5">
          {jobs.map((job) => {
            const isSelected = job.id === activeJobId
            return (
              <div
                key={job.id}
                onClick={() => onSelect(job.id)}
                className={`group flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition ${
                  isSelected
                    ? 'border-indigo-500/70 bg-indigo-500/10'
                    : 'border-slate-800/80 bg-slate-950/40 hover:border-slate-700 hover:bg-slate-900'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    {job.status === 'completed' && (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                    )}
                    {job.status === 'failed' && (
                      <AlertCircle className="h-4 w-4 shrink-0 text-rose-400" />
                    )}
                    {(job.status === 'processing' || job.status === 'queued') && (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-indigo-400" />
                    )}
                    <span className="truncate text-sm font-semibold text-white">{job.title}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 capitalize text-slate-300">
                      {job.instrument}
                    </span>
                    <span>{job.quantization}</span>
                    {job.detected_bpm && <span>{Math.round(job.detected_bpm)} BPM</span>}
                    {job.detected_key && <span>{job.detected_key}</span>}
                    {job.status === 'processing' && (
                      <span className="text-indigo-300">{job.progress}%</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="hidden items-center gap-1 text-[11px] text-slate-500 sm:flex">
                    <Clock className="h-3 w-3" />
                    {new Date(job.created_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(job.id)
                    }}
                    title="Delete transcription"
                    className="rounded-lg p-1.5 text-slate-500 opacity-70 transition hover:bg-rose-500/15 hover:text-rose-400 group-hover:opacity-100"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
