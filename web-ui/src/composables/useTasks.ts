import { ref, onMounted } from 'vue'
import type {
  Task,
  TaskCreateResponse,
  TaskGenerateRequest,
  TaskUpdate,
} from '@/types/task.d.ts'
import * as taskApi from '@/api/tasks'
import { useWebSocket } from '@/composables/useWebSocket'

const STALE_MS = 3_000

export function useTasks() {
  const tasks = ref<Task[]>([])
  const isLoading = ref(false)
  const isMutating = ref(false)
  const error = ref<Error | null>(null)
  const stoppingTaskIds = ref<Set<number>>(new Set())
  const { on } = useWebSocket()
  let lastFetchedAt = 0

  async function fetchTasks(options?: { silent?: boolean; force?: boolean }) {
    if (!options?.force && Date.now() - lastFetchedAt < STALE_MS) return
    if (!options?.silent) {
      isLoading.value = true
    }
    error.value = null
    try {
      tasks.value = await taskApi.getAllTasks()
      lastFetchedAt = Date.now()
    } catch (e) {
      if (e instanceof Error) {
        error.value = e
      }
      console.error(e)
    } finally {
      if (!options?.silent) {
        isLoading.value = false
      }
    }
  }

  on('tasks_updated', () => {
    fetchTasks({ silent: true, force: true })
  })

  on('task_status_changed', (data: unknown) => {
    const payload = data as { id: number; is_running: boolean }
    const task = tasks.value.find((t) => t.id === payload.id)
    if (task) {
      task.is_running = payload.is_running
    }
  })

  async function createTask(data: TaskGenerateRequest): Promise<TaskCreateResponse> {
    isMutating.value = true
    error.value = null
    try {
      return await taskApi.createTaskWithAI(data)
    } catch (e) {
      if (e instanceof Error) {
        error.value = e
      }
      console.error(e)
      throw e
    } finally {
      isMutating.value = false
    }
  }

  async function updateTask(taskId: number, data: TaskUpdate) {
    error.value = null
    try {
      const updatedTask = await taskApi.updateTask(taskId, data)
      const index = tasks.value.findIndex((task) => task.id === updatedTask.id)
      if (index >= 0) {
        tasks.value[index] = { ...tasks.value[index], ...updatedTask }
      } else {
        tasks.value.push(updatedTask)
      }
    } catch (e) {
      if (e instanceof Error) {
        error.value = e
      }
      console.error(e)
      throw e
    }
  }

  async function removeTask(taskId: number) {
    try {
      await taskApi.deleteTask(taskId)
      // Refresh the list after deleting
      await fetchTasks()
    } catch (e) {
      console.error(e)
      // Optionally, set the error ref to display it in the UI
      if (e instanceof Error) {
        error.value = e
      }
      throw e
    }
  }

  async function startTask(taskId: number) {
    isMutating.value = true
    const task = tasks.value.find((t) => t.id === taskId)
    const previous = task ? { is_running: task.is_running, is_queued: task.is_queued, execution_state: task.execution_state } : null
    if (task) {
      task.is_queued = true
      task.execution_state = 'queued'
    }
    try {
      await taskApi.startTask(taskId)
      // The websocket will update the status, but we can also optimistically update
    } catch (e) {
      if (task && previous) {
        task.is_running = previous.is_running
        task.is_queued = previous.is_queued
        task.execution_state = previous.execution_state
      }
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isMutating.value = false
    }
  }

  async function stopTask(taskId: number) {
    isMutating.value = true
    const next = new Set(stoppingTaskIds.value)
    next.add(taskId)
    stoppingTaskIds.value = next
    try {
      await taskApi.stopTask(taskId)
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      const cleaned = new Set(stoppingTaskIds.value)
      cleaned.delete(taskId)
      stoppingTaskIds.value = cleaned
      isMutating.value = false
    }
  }
  
  onMounted(fetchTasks)

  return {
    tasks,
    isLoading,
    isMutating,
    error,
    fetchTasks,
    createTask,
    updateTask,
    removeTask,
    startTask,
    stopTask,
    stoppingTaskIds,
  }
}
