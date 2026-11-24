import { useCallback, useEffect, useState } from 'react'
import { createJob, deleteJob, fetchHealth, getJob, listJobs } from './api'
import { Header } from './components/Header'
import { JobHistory } from './components/JobHistory'
import { ProgressTracker } from './components/ProgressTracker'
import { ScoreWorkspace } from './components/ScoreWorkspace'
import { UploadStudio } from './components/UploadStudio'
import type { CreateJobParams, HealthResponse, JobDetail, JobSummary } from './types'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [activeJob, setActiveJob] = useState<JobDetail | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const refreshJobs = useCallback(async () => {
    try {
      const items = await listJobs()
      setJobs(items)
    } catch {
      // ignore transient polling error
    }
  }, [])

  useEffect(() => {
    const loadHealth = async () => {
      try {
        const h = await fetchHealth()
        setHealth(h)
      } catch {
        setHealth(null)
      }
    }

    void loadHealth()
    void refreshJobs()

    const id = window.setInterval(() => {
      void loadHealth()
      void refreshJobs()
    }, 6000)

    return () => window.clearInterval(id)
  }, [refreshJobs])

  const handleSubmit = async (params: CreateJobParams) => {
    setIsSubmitting(true)
    setSubmitError(null)
    try {
      const detail = await createJob(params)
      setActiveJob(detail)
      await refreshJobs()
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to start transcription.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleSelectJob = async (jobId: string) => {
    try {
      const detail = await getJob(jobId)
      setActiveJob(detail)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to load transcription.')
    }
  }

  const handleCompleted = useCallback(
    async (jobId: string) => {
      const detail = await getJob(jobId)
      setActiveJob(detail)
      await refreshJobs()
    },
    [refreshJobs]
  )

  const handleDeleteJob = async (jobId: string) => {
    try {
      await deleteJob(jobId)
      if (activeJob?.id === jobId) {
        setActiveJob(null)
      }
      await refreshJobs()
    } catch {
      // ignore
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-950/30 via-slate-950 to-slate-950">
      <Header
        health={health}
        showNewButton={activeJob !== null}
        onNewTranscription={() => setActiveJob(null)}
      />

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {!activeJob ? (
          <div className="grid gap-6 lg:grid-cols-12">
            <div className="lg:col-span-8">
              <UploadStudio
                onSubmit={handleSubmit}
                isSubmitting={isSubmitting}
                error={submitError}
              />
            </div>
            <div className="lg:col-span-4">
              <JobHistory
                jobs={jobs}
                activeJobId={null}
                onSelect={handleSelectJob}
                onDelete={handleDeleteJob}
              />
            </div>
          </div>
        ) : activeJob.status === 'completed' ? (
          <ScoreWorkspace
            job={activeJob}
            onBack={() => setActiveJob(null)}
            onJobCreated={(newJob) => {
              setActiveJob(newJob)
              void refreshJobs()
            }}
          />
        ) : (
          <ProgressTracker
            job={activeJob}
            onCompleted={handleCompleted}
            onBack={() => setActiveJob(null)}
          />
        )}
      </main>
    </div>
  )
}
