import type { CreateJobParams, HealthResponse, JobDetail, JobSummary, NotesPayload } from './types'

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) return data.detail.map((d: { msg?: string }) => d.msg).join(', ')
  } catch {
    // ignore JSON error
  }
  return `Request failed (${res.status} ${res.statusText})`
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch('/api/health')
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function listJobs(): Promise<JobSummary[]> {
  const res = await fetch('/api/jobs')
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function getJob(jobId: string): Promise<JobDetail> {
  const res = await fetch(`/api/jobs/${jobId}`)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function createJob(params: CreateJobParams): Promise<JobDetail> {
  const form = new FormData()
  form.append('file', params.file)
  form.append('title', params.title || '')
  form.append('instrument', params.instrument)
  form.append('separate_stems', String(params.separate_stems))
  form.append('stem_mode', params.stem_mode || (params.separate_stems ? 'auto' : 'none'))
  form.append('quantization', params.quantization)
  form.append('time_signature', params.time_signature)
  if (params.bpm != null && !Number.isNaN(params.bpm)) {
    form.append('bpm', String(params.bpm))
  }
  if (params.key_signature) {
    form.append('key_signature', params.key_signature)
  }

  const res = await fetch('/api/jobs', {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function extractPianoOnly(
  jobId: string,
  quantization: string = '1/8'
): Promise<JobDetail> {
  const form = new FormData()
  form.append('quantization', quantization)
  const res = await fetch(`/api/jobs/${jobId}/extract-piano`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function createPianoCover(
  jobId: string,
  quantization: string = '1/8',
  timeSignature: string = 'auto'
): Promise<JobDetail> {
  const form = new FormData()
  form.append('quantization', quantization)
  form.append('time_signature', timeSignature)
  const res = await fetch(`/api/jobs/${jobId}/piano-cover`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await parseError(res))
}

export async function fetchMusicXml(url: string): Promise<string> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(await parseError(res))
  return res.text()
}

export async function fetchNotes(url: string): Promise<NotesPayload> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}
