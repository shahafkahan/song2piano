import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Music2, Tag } from 'lucide-react'
import type { TranscribedNote } from '../types'

export interface SynthesiaHandle {
  updateTime: (timeSec: number) => void
}

interface SynthesiaViewProps {
  notes: TranscribedNote[]
  bpm: number
  timeSignature: string
  beatTimes?: number[]
  beatIndices?: number[]
  currentTimeSec: number
  compact?: boolean
  onSeek?: (timeSec: number) => void
}

type HandFilter = 'both' | 'rh' | 'lh'
type RangeMode = 'auto' | 'full88'

interface KeyGeom {
  midi: number
  isBlack: boolean
  x: number
  width: number
}

const BLACK_PCS = new Set([1, 3, 6, 8, 10])
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

function isBlackKey(midi: number): boolean {
  return BLACK_PCS.has(((midi % 12) + 12) % 12)
}

function midiLabel(midi: number): string {
  const oct = Math.floor(midi / 12) - 1
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${oct}`
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
): void {
  const rad = Math.max(0, Math.min(r, w * 0.5, h * 0.5))
  ctx.beginPath()
  ctx.moveTo(x + rad, y)
  ctx.lineTo(x + w - rad, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + rad)
  ctx.lineTo(x + w, y + h - rad)
  ctx.quadraticCurveTo(x + w, y + h, x + w - rad, y + h)
  ctx.lineTo(x + rad, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - rad)
  ctx.lineTo(x, y + rad)
  ctx.quadraticCurveTo(x, y, x + rad, y)
  ctx.closePath()
}

function buildKeyGeometries(minMidi: number, maxMidi: number, canvasWidth: number): KeyGeom[] {
  const whiteMidis: number[] = []
  for (let m = minMidi; m <= maxMidi; m += 1) {
    if (!isBlackKey(m)) whiteMidis.push(m)
  }
  const whiteCount = Math.max(1, whiteMidis.length)
  const whiteW = canvasWidth / whiteCount
  const blackW = whiteW * 0.62

  const whiteXMap = new Map<number, number>()
  whiteMidis.forEach((m, idx) => {
    whiteXMap.set(m, idx * whiteW)
  })

  const keys: KeyGeom[] = []
  for (let m = minMidi; m <= maxMidi; m += 1) {
    if (!isBlackKey(m)) {
      keys.push({
        midi: m,
        isBlack: false,
        x: whiteXMap.get(m) ?? 0,
        width: whiteW,
      })
    } else {
      const prevWhiteX = whiteXMap.get(m - 1)
      const nextWhiteX = whiteXMap.get(m + 1)
      let seamX = 0
      if (prevWhiteX !== undefined) {
        seamX = prevWhiteX + whiteW
      } else if (nextWhiteX !== undefined) {
        seamX = nextWhiteX
      }
      keys.push({
        midi: m,
        isBlack: true,
        x: seamX - blackW * 0.5,
        width: blackW,
      })
    }
  }
  return keys
}

export const SynthesiaView = forwardRef<SynthesiaHandle, SynthesiaViewProps>(
  function SynthesiaView(
    { notes, bpm, timeSignature, beatTimes, beatIndices, currentTimeSec, compact = false, onSeek },
    ref
  ) {
    const wrapRef = useRef<HTMLDivElement | null>(null)
    const canvasRef = useRef<HTMLCanvasElement | null>(null)
    const latestTimeRef = useRef<number>(currentTimeSec)
    const lastHudKeyRef = useRef<string>('')

    const [handFilter, setHandFilter] = useState<HandFilter>('both')
    const [rangeMode, setRangeMode] = useState<RangeMode>('auto')
    const [showLabels, setShowLabels] = useState<boolean>(true)
    const [lookaheadSec, setLookaheadSec] = useState<number>(4.0)
    const [canvasSize, setCanvasSize] = useState<{ w: number; h: number }>({
      w: 960,
      h: compact ? 340 : 460,
    })
    const [activeHud, setActiveHud] = useState<string>('Ready')

    // Compute visible MIDI range
    const [minMidi, maxMidi] = useMemo(() => {
      if (rangeMode === 'full88' || notes.length === 0) {
        return [21, 108] as const
      }
      let lo = 108
      let hi = 21
      for (const n of notes) {
        if (n.midi < lo) lo = n.midi
        if (n.midi > hi) hi = n.midi
      }
      // Pad to C boundaries and ensure at least 36 semitones (3 octaves)
      let startC = Math.max(21, Math.floor((lo - 3) / 12) * 12)
      let endB = Math.min(108, Math.ceil((hi + 4) / 12) * 12 - 1)
      if (endB - startC < 36) {
        const extra = Math.ceil((36 - (endB - startC)) / 2)
        startC = Math.max(21, Math.floor((startC - extra) / 12) * 12)
        endB = Math.min(108, startC + 47)
      }
      if (isBlackKey(startC)) startC = Math.max(21, startC - 1)
      if (isBlackKey(endB)) endB = Math.min(108, endB + 1)
      return [startC, endB] as const
    }, [notes, rangeMode])

    const filteredNotes = useMemo(() => {
      if (handFilter === 'both') return notes
      const targetStaff = handFilter === 'rh' ? 'treble' : 'bass'
      return notes.filter((n) => n.staff === targetStaff)
    }, [notes, handFilter])

    const barBeats = useMemo(() => {
      if (timeSignature === '3/4' || timeSignature === '6/8') return 3
      if (timeSignature === '2/4') return 2
      return 4
    }, [timeSignature])

    const hasMelody = useMemo(() => notes.some((n) => n.role === 'melody'), [notes])

    // Measure grid lines in seconds
    const gridLines = useMemo(() => {
      const lines: Array<{ timeSec: number; isBar: boolean }> = []
      if (beatTimes && beatIndices && beatTimes.length >= 2 && beatTimes.length === beatIndices.length) {
        for (let i = 0; i < beatTimes.length; i += 1) {
          const t = beatTimes[i]
          if (t < 0) continue
          const bi = Math.round(beatIndices[i])
          lines.push({ timeSec: t, isBar: ((bi % barBeats) + barBeats) % barBeats === 0 })
        }
        return lines
      }
      const spb = 60 / Math.max(30, bpm || 120)
      const maxT = (notes[notes.length - 1]?.end_sec ?? 20) + 6
      for (let bi = 0; bi * spb <= maxT; bi += 1) {
        lines.push({ timeSec: bi * spb, isBar: bi % barBeats === 0 })
      }
      return lines
    }, [beatTimes, beatIndices, barBeats, bpm, notes])

    const keys = useMemo(
      () => buildKeyGeometries(minMidi, maxMidi, canvasSize.w),
      [minMidi, maxMidi, canvasSize.w]
    )

    const keyMap = useMemo(() => {
      const m = new Map<number, KeyGeom>()
      for (const k of keys) m.set(k.midi, k)
      return m
    }, [keys])

    const drawFrame = useCallback(
      (timeSec: number) => {
        latestTimeRef.current = timeSec
        const canvas = canvasRef.current
        if (!canvas) return
        const ctx = canvas.getContext('2d')
        if (!ctx) return

        const dpr = window.devicePixelRatio || 1
        const w = canvasSize.w
        const h = canvasSize.h
        const targetW = Math.round(w * dpr)
        const targetH = Math.round(h * dpr)
        if (canvas.width !== targetW || canvas.height !== targetH) {
          canvas.width = targetW
          canvas.height = targetH
        }

        ctx.save()
        ctx.scale(dpr, dpr)

        const kbHeight = Math.round(Math.min(126, Math.max(82, h * 0.25)))
        const hitY = h - kbHeight
        const pxPerSec = hitY / lookaheadSec

        // 1. Background
        const bgGrad = ctx.createLinearGradient(0, 0, 0, hitY)
        bgGrad.addColorStop(0, '#060911')
        bgGrad.addColorStop(1, '#0c1324')
        ctx.fillStyle = bgGrad
        ctx.fillRect(0, 0, w, hitY)

        // 2. Vertical octave lane guides (at C keys) & subtle black-key lanes
        for (const k of keys) {
          if (k.isBlack) {
            ctx.fillStyle = 'rgba(2, 6, 23, 0.45)'
            ctx.fillRect(k.x, 0, k.width, hitY)
          } else if (k.midi % 12 === 0) {
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.16)'
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(k.x, 0)
            ctx.lineTo(k.x, hitY)
            ctx.stroke()
          }
        }

        // 3. Horizontal beat & measure grid lines
        for (const gl of gridLines) {
          if (gl.timeSec < timeSec - 0.05 || gl.timeSec > timeSec + lookaheadSec + 0.05) continue
          const y = hitY - (gl.timeSec - timeSec) * pxPerSec
          if (y < 0 || y > hitY) continue
          ctx.strokeStyle = gl.isBar ? 'rgba(129, 140, 248, 0.28)' : 'rgba(148, 163, 184, 0.08)'
          ctx.lineWidth = gl.isBar ? 1.25 : 1
          ctx.beginPath()
          ctx.moveTo(0, y)
          ctx.lineTo(w, y)
          ctx.stroke()
        }

        // 4. Track active pressed keys & draw falling note bars
        const activeKeyRole = new Map<number, 'melody' | 'treble' | 'bass'>()
        const activePitchNames: string[] = []
        const winStart = timeSec - 0.2
        const winEnd = timeSec + lookaheadSec + 0.2

        for (let i = 0; i < filteredNotes.length; i += 1) {
          const n = filteredNotes[i]
          if (n.end_sec < winStart) continue
          if (n.start_sec > winEnd) break

          const key = keyMap.get(n.midi)
          if (!key) continue

          const isMel = n.role === 'melody'
          const isActive = n.start_sec <= timeSec && n.end_sec >= timeSec
          if (isActive) {
            const roleTag = isMel ? 'melody' : n.staff
            // Melody color wins if a key is doubled
            if (activeKeyRole.get(n.midi) !== 'melody') {
              activeKeyRole.set(n.midi, roleTag)
            }
            if (activePitchNames.length < 7 && !activePitchNames.includes(n.pitch_name)) {
              activePitchNames.push(n.pitch_name)
            }
          }

          const rawBottom = hitY - (n.start_sec - timeSec) * pxPerSec
          const rawTop = hitY - (n.end_sec - timeSec) * pxPerSec
          const yTop = Math.max(-8, rawTop)
          const yBottom = Math.min(hitY + 4, rawBottom)
          const barH = Math.max(5, yBottom - yTop)
          const pad = key.isBlack ? 1 : 1.5
          const bx = key.x + pad
          const bw = Math.max(3, key.width - pad * 2)

          const isRh = n.staff === 'treble'
          const baseColor = isMel
            ? isActive
              ? '#fbbf24'
              : key.isBlack
              ? '#d97706'
              : '#f59e0b'
            : isRh
            ? isActive
              ? '#34d399'
              : key.isBlack
              ? '#059669'
              : '#10b981'
            : isActive
            ? '#7dd3fc'
            : key.isBlack
            ? '#0284c7'
            : '#38bdf8'

          if (isActive) {
            ctx.save()
            ctx.shadowColor = isMel
              ? 'rgba(251, 191, 36, 0.85)'
              : isRh
              ? 'rgba(52, 211, 153, 0.8)'
              : 'rgba(56, 189, 248, 0.8)'
            ctx.shadowBlur = 14
            ctx.fillStyle = baseColor
            drawRoundedRect(ctx, bx, yTop, bw, barH, 4)
            ctx.fill()
            ctx.restore()
          } else {
            ctx.fillStyle = baseColor
            drawRoundedRect(ctx, bx, yTop, bw, barH, 4)
            ctx.fill()
          }

          // Subtle top sheen
          ctx.fillStyle = 'rgba(255, 255, 255, 0.22)'
          drawRoundedRect(ctx, bx + 1, yTop + 1, Math.max(1, bw - 2), Math.min(4, barH * 0.35), 2)
          ctx.fill()

          // Optional note name label inside tall/wide enough bars
          if (showLabels && barH >= 16 && bw >= 16) {
            ctx.fillStyle = isMel ? '#451a03' : isRh ? '#022c22' : '#082f49'
            ctx.font = '600 10px Inter, system-ui, sans-serif'
            ctx.textAlign = 'center'
            ctx.textBaseline = 'bottom'
            ctx.fillText(n.pitch_name, bx + bw * 0.5, yBottom - 3)
          }
        }

        // 5. Glowing hit line right above keyboard
        const hitGrad = ctx.createLinearGradient(0, hitY - 6, 0, hitY + 2)
        hitGrad.addColorStop(0, 'rgba(99, 102, 241, 0)')
        hitGrad.addColorStop(1, 'rgba(99, 102, 241, 0.55)')
        ctx.fillStyle = hitGrad
        ctx.fillRect(0, hitY - 6, w, 8)
        ctx.fillStyle = '#818cf8'
        ctx.fillRect(0, hitY - 1.5, w, 2)

        // 6. Piano keyboard (white keys first, then black keys)
        for (const k of keys) {
          if (k.isBlack) continue
          const activeRole = activeKeyRole.get(k.midi)
          const isMel = activeRole === 'melody'
          const isRh = activeRole === 'treble'

          if (activeRole) {
            ctx.fillStyle = isMel ? '#fde68a' : isRh ? '#6ee7b7' : '#7dd3fc'
          } else {
            const wg = ctx.createLinearGradient(0, hitY, 0, h)
            wg.addColorStop(0, '#e2e8f0')
            wg.addColorStop(0.12, '#ffffff')
            wg.addColorStop(1, '#cbd5e1')
            ctx.fillStyle = wg
          }
          ctx.fillRect(k.x + 0.5, hitY, Math.max(1, k.width - 1), kbHeight)

          ctx.strokeStyle = '#475569'
          ctx.lineWidth = 0.8
          ctx.strokeRect(k.x + 0.5, hitY, Math.max(1, k.width - 1), kbHeight)

          if (activeRole) {
            ctx.fillStyle = isMel ? '#f59e0b' : isRh ? '#10b981' : '#0ea5e9'
            ctx.fillRect(k.x + 1, hitY, Math.max(1, k.width - 2), 6)
          }

          if (k.midi % 12 === 0 && k.width >= 11) {
            ctx.fillStyle = activeRole ? '#0f172a' : '#64748b'
            ctx.font = '600 9px JetBrains Mono, monospace'
            ctx.textAlign = 'center'
            ctx.textBaseline = 'bottom'
            ctx.fillText(midiLabel(k.midi), k.x + k.width * 0.5, h - 5)
          }
        }

        const blackH = Math.round(kbHeight * 0.62)
        for (const k of keys) {
          if (!k.isBlack) continue
          const activeRole = activeKeyRole.get(k.midi)
          const isMel = activeRole === 'melody'
          const isRh = activeRole === 'treble'

          if (activeRole) {
            ctx.fillStyle = isMel ? '#d97706' : isRh ? '#10b981' : '#0284c7'
          } else {
            const bg = ctx.createLinearGradient(0, hitY, 0, hitY + blackH)
            bg.addColorStop(0, '#1e293b')
            bg.addColorStop(1, '#020617')
            ctx.fillStyle = bg
          }
          drawRoundedRect(ctx, k.x, hitY, k.width, blackH, 3)
          ctx.fill()
          ctx.strokeStyle = '#0f172a'
          ctx.lineWidth = 1
          ctx.stroke()

          if (activeRole) {
            ctx.fillStyle = isMel ? '#fef08a' : isRh ? '#a7f3d0' : '#bae6fd'
            ctx.fillRect(k.x + 1.5, hitY + blackH - 6, Math.max(1, k.width - 3), 4)
          }
        }

        ctx.restore()

        const hudText = activePitchNames.length > 0 ? activePitchNames.join(' · ') : '—'
        if (hudText !== lastHudKeyRef.current) {
          lastHudKeyRef.current = hudText
          setActiveHud(hudText)
        }
      },
      [canvasSize, filteredNotes, gridLines, keyMap, keys, lookaheadSec, showLabels]
    )

    useImperativeHandle(
      ref,
      () => ({
        updateTime: (timeSec: number) => {
          drawFrame(timeSec)
        },
      }),
      [drawFrame]
    )

    // Observe container width for responsive HiDPI canvas
    useEffect(() => {
      const el = wrapRef.current
      if (!el) return
      const updateSize = () => {
        const rect = el.getBoundingClientRect()
        if (rect.width > 100) {
          setCanvasSize({
            w: Math.round(rect.width),
            h: compact ? 330 : 450,
          })
        }
      }
      updateSize()
      const ro = new ResizeObserver(updateSize)
      ro.observe(el)
      return () => ro.disconnect()
    }, [compact])

    // Re-draw on state/prop updates
    useEffect(() => {
      drawFrame(currentTimeSec)
    }, [currentTimeSec, drawFrame])

    const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!onSeek || !canvasRef.current) return
      const rect = canvasRef.current.getBoundingClientRect()
      const clickY = e.clientY - rect.top
      const kbHeight = Math.round(Math.min(126, Math.max(82, canvasSize.h * 0.25)))
      const hitY = canvasSize.h - kbHeight
      if (clickY >= hitY) return
      const pxPerSec = hitY / lookaheadSec
      const deltaSec = (hitY - clickY) / pxPerSec
      onSeek(Math.max(0, latestTimeRef.current + deltaSec))
    }

    return (
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/75 px-4 py-2.5">
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="font-semibold text-white">Synthesia Roll</span>
            {hasMelody && (
              <span className="inline-flex items-center gap-1.5 text-[11px] text-amber-300">
                <span className="h-2 w-2 rounded-full bg-amber-400" />
                Vocal Melody
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-300">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              Right Hand
            </span>
            <span className="inline-flex items-center gap-1.5 text-[11px] text-sky-300">
              <span className="h-2 w-2 rounded-full bg-sky-400" />
              Left Hand
            </span>
            <span className="text-slate-600">·</span>
            <span className="inline-flex items-center gap-1 font-mono text-[11px] text-indigo-300">
              <Music2 className="h-3 w-3" />
              {activeHud}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
              {(
                [
                  ['both', 'Both'],
                  ['rh', 'RH'],
                  ['lh', 'LH'],
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

            <div className="flex items-center rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
              {(
                [
                  ['auto', 'Auto Fit'],
                  ['full88', '88 Keys'],
                ] as const
              ).map(([val, label]) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => setRangeMode(val)}
                  className={`rounded-md px-2 py-1 font-medium transition ${
                    rangeMode === val
                      ? 'bg-slate-700 text-white'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setShowLabels((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
                showLabels
                  ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-300'
                  : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'
              }`}
              title="Toggle pitch labels on falling bars"
            >
              <Tag className="h-3 w-3" />
              Labels
            </button>

            <select
              value={lookaheadSec}
              onChange={(e) => setLookaheadSec(Number(e.target.value))}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200 focus:border-indigo-500 focus:outline-none"
              title="Falling speed / lookahead window"
            >
              <option value={2.5}>Fast (2.5s)</option>
              <option value={4.0}>Normal (4.0s)</option>
              <option value={6.0}>Slow (6.0s)</option>
            </select>
          </div>
        </div>

        <div ref={wrapRef} className="w-full bg-[#060911]">
          <canvas
            ref={canvasRef}
            onClick={handleCanvasClick}
            style={{ width: `${canvasSize.w}px`, height: `${canvasSize.h}px` }}
            className="block cursor-pointer"
          />
        </div>
      </div>
    )
  }
)
