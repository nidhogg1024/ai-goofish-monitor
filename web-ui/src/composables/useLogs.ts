import { ref, onMounted, onUnmounted } from 'vue'
import * as logsApi from '@/api/logs'
import { t } from '@/i18n'

export function useLogs() {
  const logs = ref('')
  const currentPos = ref(0)
  const currentTaskId = ref<number | null>(null)
  const currentTaskIds = ref<number[]>([])
  const historyOffset = ref(0)
  const hasMoreHistory = ref(false)
  const isFetchingHistory = ref(false)
  const isAutoRefresh = ref(true)
  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  
  let refreshInterval: number | null = null
  const MAX_LOG_CHARS = 200_000
  const TRIM_LOG_CHARS = 150_000
  const TRIM_NOTICE = t('logs.trimmedNotice')

  function appendLogs(content: string) {
    if (!content) return
    logs.value += content
    // Prevent unbounded growth that can freeze the UI.
    if (logs.value.length > MAX_LOG_CHARS) {
      const tail = logs.value.slice(-TRIM_LOG_CHARS)
      logs.value = `${TRIM_NOTICE}\n${tail}`
    }
  }

  async function fetchLogs() {
    if (isLoading.value) return
    if (currentTaskId.value === null && currentTaskIds.value.length === 0) return
    isLoading.value = true
    try {
      const data = await logsApi.getLogs(currentPos.value, currentTaskId.value, currentTaskIds.value)
      if (data.new_pos < currentPos.value) {
        // Log file rotated or cleared.
        logs.value = ''
      }
      if (currentTaskIds.value.length > 1) {
        logs.value = data.new_content || ''
      } else if (data.new_content) {
        appendLogs(data.new_content)
      }
      currentPos.value = data.new_pos
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      isLoading.value = false
    }
  }

  async function loadLatest(limitLines: number = 50) {
    if (isFetchingHistory.value) return
    if (currentTaskId.value === null && currentTaskIds.value.length === 0) return
    isFetchingHistory.value = true
    try {
      const data = await logsApi.getLogTail(currentTaskId.value, 0, limitLines, currentTaskIds.value)
      logs.value = data.content || ''
      historyOffset.value = data.next_offset
      hasMoreHistory.value = data.has_more
      currentPos.value = data.new_pos
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      isFetchingHistory.value = false
    }
  }

  async function loadPrevious(limitLines: number = 50) {
    if (isFetchingHistory.value) return
    if (!hasMoreHistory.value) return
    if (currentTaskId.value === null && currentTaskIds.value.length === 0) return
    isFetchingHistory.value = true
    try {
      const data = await logsApi.getLogTail(currentTaskId.value, historyOffset.value, limitLines, currentTaskIds.value)
      if (data.content) {
        logs.value = logs.value ? `${data.content}\n${logs.value}` : data.content
      }
      historyOffset.value = data.next_offset
      hasMoreHistory.value = data.has_more
      currentPos.value = data.new_pos
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      isFetchingHistory.value = false
    }
  }

  async function clearLogs() {
    try {
      if (currentTaskId.value === null && currentTaskIds.value.length === 0) return
      await logsApi.clearLogs(currentTaskId.value, currentTaskIds.value)
      logs.value = ''
      currentPos.value = 0
      historyOffset.value = 0
      hasMoreHistory.value = false
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    }
  }

  function scheduleNextRefresh() {
    if (refreshInterval) return
    refreshInterval = window.setTimeout(async () => {
      refreshInterval = null
      await fetchLogs()
      if (isAutoRefresh.value) {
        scheduleNextRefresh()
      }
    }, 2000)
  }

  function startAutoRefresh() {
    if (isAutoRefresh.value && refreshInterval) return
    isAutoRefresh.value = true
    fetchLogs()
    scheduleNextRefresh()
  }

  function stopAutoRefresh() {
    if (refreshInterval) {
      clearTimeout(refreshInterval)
      refreshInterval = null
    }
    isAutoRefresh.value = false
  }

  function toggleAutoRefresh() {
    if (isAutoRefresh.value) {
      stopAutoRefresh()
    } else {
      startAutoRefresh()
    }
  }

  function setScope(taskId: number | null, taskIds: number[] = []) {
    const normalizedTaskIds = [...taskIds].sort((a, b) => a - b)
    const unchanged =
      currentTaskId.value === taskId &&
      currentTaskIds.value.length === normalizedTaskIds.length &&
      currentTaskIds.value.every((value, index) => value === normalizedTaskIds[index])
    if (unchanged) return
    currentTaskId.value = taskId
    currentTaskIds.value = normalizedTaskIds
    logs.value = ''
    currentPos.value = 0
    historyOffset.value = 0
    hasMoreHistory.value = false
  }

  onMounted(() => {
    startAutoRefresh()
  })

  onUnmounted(() => {
    stopAutoRefresh()
  })

  return {
    logs,
    isAutoRefresh,
    isLoading, // Not strictly used for polling to avoid flickering
    isFetchingHistory,
    hasMoreHistory,
    error,
    fetchLogs,
    clearLogs,
    toggleAutoRefresh,
    setScope,
    loadLatest,
    loadPrevious
  }
}
