import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { OpenSheetMusicDisplay } from 'opensheetmusicdisplay'
import { Eye, Maximize2, Minimize2, Music2, ZoomIn, ZoomOut } from 'lucide-react'
import type { InstrumentType, TranscribedNote } from '../types'

export interface SheetMusicHandle {
  updateTime: (timeSec: number) => void
}

interface PlayheadStep {
  timeSec: number
  x: number
  top: number
  height: number
  measureNumber: number
}

type HandFilter = 'both' | 'rh' | 'lh'
type PaperTheme = 'ivory' | 'white' | 'night'

interface SheetMusicViewProps {
  musicXml: string
  bpm: number
  instrument: InstrumentType
  notes: TranscribedNote[]
  beatTimes?: number[]
  beatIndices?: number[]
  currentTimeSec: number
  onSeek?: (timeSec: number) => void
}

function interpBeatToSec(
  beat: number,
  beatIndices?: number[],
  beatTimes?: number[],
  bpm = 120
): number {
  if (!beatIndices || !beatTimes || beatIndices.length < 2 || beatIndices.length !== beatTimes.length) {
    return beat * (60 / Math.max(30, bpm || 120))
  }
  const n = beatIndices.length
  if (beat <= beatIndices[0]) {
    const dt = beatTimes[1] - beatTimes[0]
    const db = beatIndices[1] - beatIndices[0] || 1
    return beatTimes[0] + ((beat - beatIndices[0]) * dt) / db
  }
  if (beat >= beatIndices[n - 1]) {
    const dt = beatTimes[n - 1] - beatTimes[n - 2]
    const db = beatIndices[n - 1] - beatIndices[n - 2] || 1
    return beatTimes[n - 1] + ((beat - beatIndices[n - 1]) * dt) / db
  }
  let lo = 0
  let hi = n - 1
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1
    if (beatIndices[mid] <= beat) lo = mid
    else hi = mid
  }
  const span = beatIndices[hi] - beatIndices[lo] || 1
  const alpha = (beat - beatIndices[lo]) / span
  return beatTimes[lo] + alpha * (beatTimes[hi] - beatTimes[lo])
}

function applyEngravingRules(osmd: OpenSheetMusicDisplay): void {
  const r = osmd.EngravingRules
  r.PageTopMargin = 3.0
  r.PageBottomMargin = 4.0
  r.PageLeftMargin = 3.8
  r.PageRightMargin = 3.8
  r.BetweenStaffDistance = 5.5
  r.StaffDistance = 6.5
  r.MinimumDistanceBetweenSystems = 8.0
  r.MinSkyBottomDistBetweenSystems = 4.2
  r.DefaultFontFamily = 'Georgia, "Times New Roman", serif'
  r.RenderPartAbbreviations = true
  r.RenderTitle = true
  r.RenderComposer = true
}

export const SheetMusicView = forwardRef<SheetMusicHandle, SheetMusicViewProps>(
  function SheetMusicView(
    { musicXml, bpm, instrument, notes, beatTimes, beatIndices, currentTimeSec, onSeek },
    ref
  ) {
    const scrollRef = useRef<HTMLDivElement | null>(null)
    const canvasRef = useRef<HTMLDivElement | null>(null)
    const osmdHostRef = useRef<HTMLDivElement | null>(null)
    const playheadRef = useRef<HTMLDivElement | null>(null)

    const osmdRef = useRef<OpenSheetMusicDisplay | null>(null)
    const stepsRef = useRef<PlayheadStep[]>([])
    const lastScrollRowTopRef = useRef<number>(-999)
    const lastHudKeyRef = useRef<string>('')
    const latestTimeRef = useRef<number>(currentTimeSec)
    const autoFollowRef = useRef<boolean>(true)
    const notesRef = useRef<TranscribedNote[]>(notes)
    notesRef.current = notes

    const [zoom, setZoom] = useState<number>(0.92)
    const [handFilter, setHandFilter] = useState<HandFilter>('both')
    const [theme, setTheme] = useState<PaperTheme>('ivory')
    const [autoFollow, setAutoFollow] = useState<boolean>(true)
    const [expanded, setExpanded] = useState<boolean>(false)
    const [hasTwoStaves, setHasTwoStaves] = useState<boolean>(false)
    const [rendering, setRendering] = useState<boolean>(true)
    const [renderError, setRenderError] = useState<string | null>(null)
    const [stepCount, setStepCount] = useState<number>(0)
    const [hud, setHud] = useState<{ bar: number; pitches: string }>({ bar: 1, pitches: 'Ready' })

    autoFollowRef.current = autoFollow

    const applyPlayheadAtTime = useCallback((timeSec: number) => {
      latestTimeRef.current = timeSec
      const steps = stepsRef.current
      const lineEl = playheadRef.current
      if (!lineEl || steps.length === 0) return

      // Binary search last step <= timeSec + slight lookahead
      const t = Math.max(0, timeSec)
      let lo = 0
      let hi = steps.length - 1
      let idx = 0
      while (lo <= hi) {
        const mid = (lo + hi) >> 1
        if (steps[mid].timeSec <= t) {
          idx = mid
          lo = mid + 1
        } else {
          hi = mid - 1
        }
      }

      const cur = steps[idx]
      let x = cur.x
      if (idx + 1 < steps.length) {
        const nxt = steps[idx + 1]
        const sameSystem = Math.abs(nxt.top - cur.top) < 28
        const span = nxt.timeSec - cur.timeSec
        if (sameSystem && span > 0.01) {
          const alpha = Math.max(0, Math.min(1, (t - cur.timeSec) / span))
          x = cur.x + alpha * (nxt.x - cur.x)
        }
      }

      lineEl.style.opacity = '1'
      lineEl.style.transform = `translate3d(${x.toFixed(1)}px, ${cur.top.toFixed(1)}px, 0)`
      lineEl.style.height = `${cur.height.toFixed(1)}px`

      if (autoFollowRef.current && scrollRef.current) {
        if (Math.abs(cur.top - lastScrollRowTopRef.current) > 28) {
          lastScrollRowTopRef.current = cur.top
          scrollRef.current.scrollTo({
            top: Math.max(0, cur.top - 120),
            behavior: 'smooth',
          })
        }
      }

      // Compute sounding notes around t for HUD without spamming React state
      const activePitches: string[] = []
      const curNotes = notesRef.current
      for (let i = 0; i < curNotes.length; i += 1) {
        const n = curNotes[i]
        if (n.start_sec <= t + 0.03 && n.end_sec >= t - 0.02) {
          if (!activePitches.includes(n.pitch_name)) {
            activePitches.push(n.pitch_name)
          }
          if (activePitches.length >= 6) break
        }
      }
      const pitchText = activePitches.length > 0 ? activePitches.join(' · ') : '—'
      const hudKey = `${cur.measureNumber}|${pitchText}`
      if (hudKey !== lastHudKeyRef.current) {
        lastHudKeyRef.current = hudKey
        setHud({ bar: cur.measureNumber, pitches: pitchText })
      }
    }, [])

    useImperativeHandle(
      ref,
      () => ({
        updateTime: (timeSec: number) => {
          applyPlayheadAtTime(timeSec)
        },
      }),
      [applyPlayheadAtTime]
    )

    const measureCursorSteps = useCallback(
      (osmd: OpenSheetMusicDisplay) => {
        const host = osmdHostRef.current
        if (!host) return
        const baseRect = host.getBoundingClientRect()
        const steps: PlayheadStep[] = []

        osmd.cursor.reset()
        osmd.cursor.show()
        while (!osmd.cursor.Iterator.EndReached) {
          const el = osmd.cursor.cursorElement
          const ts = osmd.cursor.Iterator.currentTimeStamp
          const quarterBeats = (ts?.RealValue ?? 0) * 4
          const timeSec = interpBeatToSec(quarterBeats, beatIndices, beatTimes, bpm)
          const measureNumber = (osmd.cursor.Iterator.CurrentMeasureIndex ?? 0) + 1

          if (el) {
            const rect = el.getBoundingClientRect()
            if (rect.height > 0) {
              steps.push({
                timeSec,
                x: rect.left - baseRect.left + Math.min(4, rect.width * 0.25),
                top: rect.top - baseRect.top,
                height: Math.max(44, rect.height),
                measureNumber,
              })
            }
          }
          osmd.cursor.next()
        }
        osmd.cursor.reset()
        osmd.cursor.hide()

        stepsRef.current = steps
        setStepCount(steps.length)
        lastScrollRowTopRef.current = -999
        applyPlayheadAtTime(latestTimeRef.current)
      },
      [applyPlayheadAtTime, beatIndices, beatTimes, bpm]
    )

    // Render MusicXML + apply hand filter
    useEffect(() => {
      const host = osmdHostRef.current
      if (!host || !musicXml) return

      let cancelled = false
      setRendering(true)
      setRenderError(null)

      const renderScore = async () => {
        try {
          host.innerHTML = ''
          const osmd = new OpenSheetMusicDisplay(host, {
            autoResize: false,
            backend: 'svg',
            autoBeam: true,
            drawTitle: true,
            drawComposer: true,
            drawCredits: false,
            drawPartNames: true,
            drawPartAbbreviations: false,
            drawMeasureNumbers: true,
            measureNumberInterval: 4,
            followCursor: false,
            drawingParameters: 'default',
          })
          applyEngravingRules(osmd)
          osmd.Zoom = zoom
          await osmd.load(musicXml)
          if (cancelled) return

          // Detect 2-staff piano layout & apply RH/LH visibility filter
          const instruments = osmd.Sheet?.Instruments ?? []
          let twoStaves = false
          if (instruments.length >= 2) {
            twoStaves = true
            instruments[0].Visible = handFilter === 'both' || handFilter === 'rh'
            instruments[1].Visible = handFilter === 'both' || handFilter === 'lh'
          } else if (instruments.length === 1 && (instruments[0].Staves?.length ?? 0) >= 2) {
            twoStaves = true
            instruments[0].Staves[0].Visible = handFilter === 'both' || handFilter === 'rh'
            instruments[0].Staves[1].Visible = handFilter === 'both' || handFilter === 'lh'
          }
          setHasTwoStaves(twoStaves && instrument === 'piano')

          osmd.render()
          osmdRef.current = osmd
          measureCursorSteps(osmd)
        } catch (err) {
          if (!cancelled) {
            setRenderError(err instanceof Error ? err.message : 'Failed to render sheet music.')
          }
        } finally {
          if (!cancelled) setRendering(false)
        }
      }

      void renderScore()

      return () => {
        cancelled = true
        osmdRef.current = null
      }
    }, [musicXml, bpm, beatTimes, beatIndices, zoom, handFilter, instrument, measureCursorSteps])

    // Re-layout cleanly on container resize
    useEffect(() => {
      const host = osmdHostRef.current
      if (!host) return
      let timer: number | null = null
      const ro = new ResizeObserver(() => {
        if (timer !== null) window.clearTimeout(timer)
        timer = window.setTimeout(() => {
          const osmd = osmdRef.current
          if (osmd) {
            osmd.render()
            measureCursorSteps(osmd)
          }
        }, 250)
      })
      ro.observe(host)
      return () => {
        if (timer !== null) window.clearTimeout(timer)
        ro.disconnect()
      }
    }, [measureCursorSteps])

    // Fallback sync when paused / scrubbed via React prop
    useEffect(() => {
      applyPlayheadAtTime(currentTimeSec)
    }, [currentTimeSec, applyPlayheadAtTime])

    const handleScoreClick = (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onSeek || stepsRef.current.length === 0 || !osmdHostRef.current) return
      const rect = osmdHostRef.current.getBoundingClientRect()
      const clickX = e.clientX - rect.left
      const clickY = e.clientY - rect.top

      let bestStep: PlayheadStep | null = null
      let bestScore = Infinity
      for (const s of stepsRef.current) {
        const yDist =
          clickY >= s.top - 16 && clickY <= s.top + s.height + 16
            ? 0
            : Math.min(Math.abs(clickY - s.top), Math.abs(clickY - (s.top + s.height)))
        const xDist = Math.abs(clickX - s.x)
        const total = yDist * 12 + xDist
        if (total < bestScore) {
          bestScore = total
          bestStep = s
        }
      }
      if (bestStep) {
        onSeek(bestStep.timeSec)
      }
    }

    const paperBg =
      theme === 'white' ? 'bg-white' : theme === 'night' ? 'bg-slate-950' : 'bg-[#fffdf8]'

    return (
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/75 px-4 py-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
              <span className="inline-block h-2 w-2 rounded-full bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.9)]" />
              <span>Bar {hud.bar}</span>
              <span className="text-slate-600">·</span>
              <span className="inline-flex items-center gap-1 font-mono text-[11px] text-indigo-300">
                <Music2 className="h-3 w-3" />
                {hud.pitches}
              </span>
              {rendering ? (
                <span className="text-slate-500">· Engraving...</span>
              ) : (
                <span className="hidden text-slate-500 sm:inline">· {stepCount} beats · Click sheet to jump</span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Hand filter for Piano */}
            {hasTwoStaves && (
              <div className="flex items-center rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
                {(
                  [
                    ['both', 'Both Hands'],
                    ['rh', 'Right Hand'],
                    ['lh', 'Left Hand'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    type="button"
                    onClick={() => setHandFilter(val)}
                    className={`rounded-md px-2 py-1 font-medium transition ${
                      handFilter === val
                        ? 'bg-indigo-600 text-white'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}

            {/* Paper Theme */}
            <div className="flex items-center rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
              {(
                [
                  ['ivory', 'Ivory'],
                  ['white', 'White'],
                  ['night', 'Night'],
                ] as const
              ).map(([val, label]) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => setTheme(val)}
                  className={`rounded-md px-2 py-1 font-medium transition ${
                    theme === val
                      ? 'bg-slate-700 text-white'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Auto-Follow */}
            <button
              type="button"
              onClick={() => setAutoFollow((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
                autoFollow
                  ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-300'
                  : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'
              }`}
              title="Auto-scroll sheet to follow playhead line"
            >
              <Eye className="h-3.5 w-3.5" />
              Follow
            </button>

            {/* Zoom */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setZoom((z) => Math.max(0.55, Number((z - 0.08).toFixed(2))))}
                className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-300 transition hover:bg-slate-800"
                title="Zoom out"
              >
                <ZoomOut className="h-3.5 w-3.5" />
              </button>
              <span className="min-w-[3rem] text-center font-mono text-xs text-slate-300">
                {Math.round(zoom * 100)}%
              </span>
              <button
                type="button"
                onClick={() => setZoom((z) => Math.min(1.4, Number((z + 0.08).toFixed(2))))}
                className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-300 transition hover:bg-slate-800"
                title="Zoom in"
              >
                <ZoomIn className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Expand */}
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-300 transition hover:bg-slate-800"
              title={expanded ? 'Collapse sheet height' : 'Expand sheet view'}
            >
              {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {renderError ? (
          <div className="p-8 text-center text-sm text-rose-300">{renderError}</div>
        ) : (
          <div
            ref={scrollRef}
            className={`${
              expanded ? 'max-h-none' : 'max-h-[70vh]'
            } overflow-y-auto ${paperBg} p-4 transition-colors sm:p-8`}
          >
            <div
              ref={canvasRef}
              onClick={handleScoreClick}
              className="relative mx-auto max-w-5xl cursor-pointer select-none"
            >
              {/* OSMD SVG host */}
              <div
                ref={osmdHostRef}
                className={theme === 'night' ? 'invert hue-rotate-180 contrast-95' : ''}
              />

              {/* Crisp 60 FPS vertical playhead needle + active-note highlight band */}
              <div
                ref={playheadRef}
                aria-hidden="true"
                style={{
                  opacity: 0,
                  transform: 'translate3d(0px, 0px, 0)',
                  height: '96px',
                  willChange: 'transform, height',
                }}
                className="pointer-events-none absolute left-0 top-0 z-20 -ml-[10px] flex w-[20px] justify-center"
              >
                <div className="h-full w-[18px] rounded bg-indigo-500/15" />
                <div className="absolute left-1/2 top-0 h-full w-[2.5px] -translate-x-1/2 rounded-full bg-indigo-600 shadow-[0_0_12px_rgba(79,70,229,0.85)]">
                  <div className="absolute -left-[3.5px] -top-[4px] h-2.5 w-2.5 rotate-45 rounded-[1px] bg-indigo-600 shadow" />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }
)
