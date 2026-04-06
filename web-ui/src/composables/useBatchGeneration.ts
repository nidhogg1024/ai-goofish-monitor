import { computed, onScopeDispose, ref } from 'vue'
import { getBatchGenerationJob } from '@/api/batchTasks'
import type { BatchGenerationJob } from '@/types/task.d.ts'

const POLL_INTERVAL_MS = 800

function isTerminalStatus(status: BatchGenerationJob['status']) {
  return status === 'completed' || status === 'failed'
}

export function useBatchGeneration() {
  const activeJob = ref<BatchGenerationJob | null>(null)
  const pollingError = ref<Error | null>(null)
  const isPolling = ref(false)
  let pollTimer: ReturnType<typeof window.setTimeout> | null = null

  function clearTimer() {
    if (pollTimer === null) return
    window.clearTimeout(pollTimer)
    pollTimer = null
  }

  async function refreshJob() {
    if (!activeJob.value) return
    try {
      const nextJob = await getBatchGenerationJob(activeJob.value.job_id)
      activeJob.value = nextJob
      pollingError.value = null
      if (isTerminalStatus(nextJob.status)) {
        isPolling.value = false
        clearTimer()
        return
      }
      scheduleNextPoll()
    } catch (error) {
      isPolling.value = false
      clearTimer()
      pollingError.value = error as Error
    }
  }

  function scheduleNextPoll() {
    clearTimer()
    pollTimer = window.setTimeout(() => {
      void refreshJob()
    }, POLL_INTERVAL_MS)
  }

  function beginPolling(job: BatchGenerationJob) {
    activeJob.value = job
    pollingError.value = null
    if (isTerminalStatus(job.status)) {
      isPolling.value = false
      clearTimer()
      return
    }
    isPolling.value = true
    scheduleNextPoll()
  }

  function clearJob() {
    activeJob.value = null
    pollingError.value = null
    isPolling.value = false
    clearTimer()
  }

  onScopeDispose(clearJob)

  return {
    activeJob,
    pollingError,
    isAnalyzing: computed(() => isPolling.value),
    beginPolling,
    clearJob,
    refreshJob,
  }
}
