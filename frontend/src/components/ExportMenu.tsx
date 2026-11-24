import { useState } from 'react'
import { ChevronDown, Download, FileCode2, FileText, Layers, Music } from 'lucide-react'
import type { ArtifactUrls } from '../types'

interface ExportMenuProps {
  artifacts: ArtifactUrls
}

export function ExportMenu({ artifacts }: ExportMenuProps) {
  const [open, setOpen] = useState(false)
  const stemEntries = Object.entries(artifacts.stems || {})

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-lg shadow-indigo-500/20 transition hover:bg-indigo-500"
      >
        <Download className="h-4 w-4" />
        Export
        <ChevronDown className="h-3.5 w-3.5" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-2xl">
            <div className="px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Sheet Music & MIDI
            </div>
            {artifacts.pdf && (
              <a
                href={`${artifacts.pdf}?download=1`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <FileText className="h-4 w-4 text-rose-400" />
                PDF Sheet Music
              </a>
            )}
            {artifacts.musicxml && (
              <a
                href={`${artifacts.musicxml}?download=1`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <FileCode2 className="h-4 w-4 text-sky-400" />
                MusicXML (.musicxml)
              </a>
            )}
            {artifacts.midi && (
              <a
                href={`${artifacts.midi}?download=1`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <Music className="h-4 w-4 text-emerald-400" />
                MIDI (Expressive Raw)
              </a>
            )}
            {artifacts.quantized_midi && (
              <a
                href={`${artifacts.quantized_midi}?download=1`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <Music className="h-4 w-4 text-indigo-400" />
                MIDI (Quantized Grid)
              </a>
            )}

            {stemEntries.length > 0 && (
              <>
                <div className="my-1.5 border-t border-slate-800" />
                <div className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  <Layers className="h-3 w-3" />
                  Isolated Stems (MP3)
                </div>
                {stemEntries.map(([name, url]) => (
                  <a
                    key={name}
                    href={`${url}?download=1`}
                    onClick={() => setOpen(false)}
                    className="flex items-center justify-between rounded-lg px-2.5 py-1.5 text-xs capitalize text-slate-300 hover:bg-slate-800"
                  >
                    <span>{name} Stem</span>
                    <Download className="h-3.5 w-3.5 text-slate-500" />
                  </a>
                ))}
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
