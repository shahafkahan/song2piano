import { Cpu, Music2, Plus, Sparkles, WifiOff } from 'lucide-react'
import type { HealthResponse } from '../types'

interface HeaderProps {
  health: HealthResponse | null
  onNewTranscription: () => void
  showNewButton: boolean
}

export function Header({ health, onNewTranscription, showNewButton }: HeaderProps) {
  const worker = health?.worker
  const online = Boolean(worker?.worker_online)
  const isGpu = worker?.device === 'cuda' || worker?.device === 'mps'

  return (
    <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
        <button
          type="button"
          onClick={onNewTranscription}
          className="group flex items-center gap-3 text-left"
        >
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-500/25 ring-1 ring-white/10">
            <Music2 className="h-5 w-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg font-bold tracking-tight text-white">SongScribe</span>
              <span className="rounded-full border border-indigo-400/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                Open Source
              </span>
            </div>
            <p className="text-xs text-slate-400">Audio to Interactive Sheet Music, MIDI & Stems</p>
          </div>
        </button>

        <div className="flex items-center gap-3">
          <div
            className={`hidden items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium sm:flex ${
              online
                ? isGpu
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : 'border-sky-500/30 bg-sky-500/10 text-sky-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            }`}
            title={
              online
                ? `Worker online (${worker?.device_name}) | ONNX: ${worker?.onnx_providers.join(', ')}`
                : 'Waiting for ML worker heartbeat'
            }
          >
            {online ? (
              isGpu ? (
                <Sparkles className="h-3.5 w-3.5" />
              ) : (
                <Cpu className="h-3.5 w-3.5" />
              )
            ) : (
              <WifiOff className="h-3.5 w-3.5" />
            )}
            <span>
              {online
                ? `${worker?.device.toUpperCase()} · ${worker?.device_name}`
                : 'Worker Connecting...'}
            </span>
          </div>

          {showNewButton && (
            <button
              type="button"
              onClick={onNewTranscription}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-indigo-500"
            >
              <Plus className="h-4 w-4" />
              New Transcription
            </button>
          )}
        </div>
      </div>
    </header>
  )
}
