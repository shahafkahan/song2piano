export type InstrumentType = 'piano' | 'guitar' | 'vocals' | 'bass' | 'strings' | 'other'
export type QuantizationType = 'auto' | '1/4' | '1/8' | '1/16' | '1/32' | 'triplet'
export type StemModeType = 'piano_cover' | 'piano_only' | 'auto' | 'none'
export type JobStatusType = 'queued' | 'processing' | 'completed' | 'failed'

export interface JobEvent {
  id: number
  job_id: string
  stage: string
  progress: number
  status: JobStatusType
  message: string
  device?: string | null
  error_message?: string | null
  created_at: string
}

export interface ArtifactUrls {
  audio: string | null
  musicxml: string | null
  pdf: string | null
  midi: string | null
  quantized_midi: string | null
  notes: string | null
  stems: Record<string, string>
}

export interface JobSummary {
  id: string
  title: string
  original_filename: string
  status: JobStatusType
  stage: string
  progress: number
  message: string
  error_message?: string | null
  instrument: InstrumentType
  separate_stems: boolean
  stem_mode?: StemModeType
  quantization: QuantizationType
  detected_bpm?: number | null
  detected_key?: string | null
  time_signature: string
  duration_seconds?: number | null
  note_count?: number | null
  measure_count?: number | null
  compute_device?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
}

export interface JobDetail extends JobSummary {
  requested_bpm?: number | null
  requested_time_signature: string
  requested_key_signature?: string | null
  artifacts: ArtifactUrls
  events: JobEvent[]
}

export interface WorkerInfo {
  worker_online: boolean
  device: string
  device_name: string
  cuda_available: boolean
  mps_available: boolean
  onnx_providers: string[]
  last_seen?: string | null
}

export interface HealthResponse {
  status: string
  database: string
  queue_backend: string
  storage_backend: string
  worker: WorkerInfo
}

export interface TranscribedNote {
  midi: number
  pitch_name: string
  start_beat: number
  duration_beats: number
  start_sec: number
  end_sec: number
  velocity: number
  staff: 'treble' | 'bass'
  role?: 'melody' | 'accompaniment' | 'bass'
}

export interface NotesPayload {
  bpm: number
  key_signature: string
  time_signature: string
  instrument: InstrumentType
  quantization: QuantizationType
  note_count: number
  measure_count: number
  beat_times?: number[]
  beat_indices?: number[]
  notes: TranscribedNote[]
}

export interface CreateJobParams {
  file: File
  title?: string
  instrument: InstrumentType
  separate_stems: boolean
  stem_mode?: StemModeType
  quantization: QuantizationType
  bpm?: number | null
  time_signature: string
  key_signature?: string
}
