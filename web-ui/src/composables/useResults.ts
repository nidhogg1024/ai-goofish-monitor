import { ref, reactive, watch, onMounted, onScopeDispose, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { ResultInsights, ResultItem } from '@/types/result.d.ts'
import * as resultsApi from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'
import * as tasksApi from '@/api/tasks'
import type { Task } from '@/types/task.d.ts'

type ResultsFilterState = {
  recommended_only: boolean
  ai_recommended_only: boolean
  keyword_recommended_only: boolean
  sort_by: 'crawl_time' | 'publish_time' | 'price' | 'keyword_hit_count'
  sort_order: 'asc' | 'desc'
}

export function useResults() {
  const { t } = useI18n()
  const route = useRoute()
  // State
  const files = ref<string[]>([])
  const selectedFile = ref<string | null>(null)
  const results = ref<ResultItem[]>([])
  const insights = ref<ResultInsights | null>(null)
  const totalItems = ref(0)
  const page = ref(1)
  const limit = ref(100)
  const taskNameByKeyword = ref<Record<string, string>>({})
  const taskMetaByKeyword = ref<Record<string, { taskName: string; category: string; groupName: string }>>({})
  const taskCatalog = ref<Task[]>([])
  const selectedCategory = ref<string | null>(null)
  const selectedGroup = ref<string | null>(null)
  const selectedTaskName = ref<string | null>(null)
  const isFileOptionsReady = ref(false)
  const hasFetchedFiles = ref(false)
  const hasFetchedTasks = ref(false)
  const readyDelayMs = 200
  let readyTimer: ReturnType<typeof setTimeout> | null = null
  
  const filters = reactive<ResultsFilterState>({
    recommended_only: false,
    ai_recommended_only: false,
    keyword_recommended_only: false,
    sort_by: 'crawl_time',
    sort_order: 'desc',
  })

  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const { on } = useWebSocket()

  function normalizeKeyword(value: string) {
    return value.trim().toLowerCase().replace(/\s+/g, '_')
  }

  function getKeywordFromFilename(filename: string) {
    return filename.replace(/_full_data\.jsonl$/i, '').toLowerCase()
  }

  function buildFilenameFromKeyword(keyword: string) {
    return `${String(keyword || '').replace(/\s+/g, '_')}_full_data.jsonl`
  }

  // Methods
  async function fetchFiles() {
    try {
      const fileList = await resultsApi.getResultFiles()
      files.value = fileList
      // 结果页的主导航现在由 分类/任务组/任务 驱动，不再默认接管旧文件选择。
      if (selectedFile.value && fileList.includes(selectedFile.value)) {
        return
      }
      selectedFile.value = null
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      hasFetchedFiles.value = true
      scheduleFileOptionsReady()
    }
  }

  async function fetchResults() {
    isLoading.value = true
    error.value = null
    try {
      const data = await resultsApi.getScopedResultContent({
        ...filters,
        page: page.value,
        limit: limit.value,
        category: selectedCategory.value,
        group_name: selectedGroup.value,
        task_name: selectedTaskName.value,
      })
      results.value = data.items
      totalItems.value = data.total_items
    } catch (e) {
      if (e instanceof Error) error.value = e
      results.value = []
      totalItems.value = 0
    } finally {
      isLoading.value = false
    }
  }

  async function fetchInsights() {
    try {
      insights.value = await resultsApi.getScopedResultInsights({
        category: selectedCategory.value,
        group_name: selectedGroup.value,
        task_name: selectedTaskName.value,
      })
    } catch (e) {
      if (e instanceof Error) error.value = e
      insights.value = null
    }
  }

  async function fetchTaskNameMap() {
    try {
      const tasks = await tasksApi.getAllTasks()
      taskCatalog.value = tasks
      const mapping: Record<string, string> = {}
      const metaMapping: Record<string, { taskName: string; category: string; groupName: string }> = {}
      tasks.forEach((task) => {
        if (task.keyword) {
          const key = normalizeKeyword(task.keyword)
          mapping[key] = task.task_name
          metaMapping[key] = {
            taskName: task.task_name,
            category: task.category || '未分类',
            groupName: task.group_name || '默认任务组',
          }
        }
      })
      taskNameByKeyword.value = mapping
      taskMetaByKeyword.value = metaMapping
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      hasFetchedTasks.value = true
      scheduleFileOptionsReady()
    }
  }

  function scheduleFileOptionsReady() {
    if (isFileOptionsReady.value || !hasFetchedFiles.value || !hasFetchedTasks.value) return
    if (readyTimer) return
    readyTimer = setTimeout(() => {
      isFileOptionsReady.value = true
      readyTimer = null
    }, readyDelayMs)
  }

  // Real-time updates
  on('results_updated', async () => {
    const oldFile = selectedFile.value
    await fetchFiles()
    // If the selected file remains the same, refresh its content (in case of append)
    // If it changed (e.g. from null to new file), the watcher will handle it.
    if (selectedFile.value && selectedFile.value === oldFile) {
      fetchResults()
      fetchInsights()
    }
  })

  on('tasks_updated', () => {
    fetchTaskNameMap()
  })

  async function refreshResults() {
    await fetchFiles()
    await fetchResults()
    await fetchInsights()
  }

  function exportSelectedResults() {
    if (!selectedFile.value) return
    resultsApi.downloadResultExport(selectedFile.value, { ...filters })
  }

  async function deleteSelectedFile(filename?: string) {
    const target = filename || selectedFile.value
    if (!target) return
    isLoading.value = true
    error.value = null
    try {
      await resultsApi.deleteResultFile(target)
      if (selectedFile.value === target) {
        const lastSelected = localStorage.getItem('lastSelectedResultFile')
        if (lastSelected === target) {
          localStorage.removeItem('lastSelectedResultFile')
        }
      }
      await fetchFiles()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoading.value = false
    }
  }

  const fileOptions = computed(() =>
    taskCatalog.value.map((task) => {
      const keyword = normalizeKeyword(task.keyword || '')
      const file = files.value.find((candidate) => getKeywordFromFilename(candidate) === keyword)
        || buildFilenameFromKeyword(task.keyword || '')
      return {
        value: `task:${task.id}`,
        taskId: task.id,
        taskName: task.task_name || t('common.unnamed'),
        keyword: task.keyword || '',
        category: task.category || '未分类',
        groupName: task.group_name || '默认任务组',
        filename: file,
        label: t('results.filters.taskNameLabel', {
          task: task.task_name || t('common.unnamed'),
        }),
      }
    })
  )
  const categoryOptions = computed(() =>
    Array.from(new Set(fileOptions.value.map((option) => option.category))).sort((a, b) =>
      a.localeCompare(b, 'zh-CN')
    )
  )
  const groupOptions = computed(() => {
    const category = selectedCategory.value
    const groups = fileOptions.value
      .filter((option) => !category || option.category === category)
      .map((option) => option.groupName)
    return Array.from(new Set(groups)).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  })
  const taskOptions = computed(() =>
    fileOptions.value.filter((option) => {
      const categoryMatch = !selectedCategory.value || option.category === selectedCategory.value
      const groupMatch = !selectedGroup.value || option.groupName === selectedGroup.value
      return categoryMatch && groupMatch
    })
  )

  let _filterDebounce: ReturnType<typeof setTimeout> | null = null
  watch([selectedCategory, selectedGroup, selectedTaskName, filters], () => {
    if (_filterDebounce) clearTimeout(_filterDebounce)
    _filterDebounce = setTimeout(() => {
      _filterDebounce = null
      fetchResults()
      fetchInsights()
    }, 300)
  }, { deep: true })
  watch(selectedFile, (value) => {
    if (value) localStorage.setItem('lastSelectedResultFile', value)
    if (!value) {
      selectedCategory.value = null
      selectedGroup.value = null
      selectedTaskName.value = null
      return
    }
    const keyword = getKeywordFromFilename(value)
    const meta = taskMetaByKeyword.value[keyword]
    if (!meta) return
    selectedTaskName.value = meta.taskName
    selectedCategory.value = meta.category
    selectedGroup.value = meta.groupName
  })
  watch([selectedCategory, groupOptions], ([category, groups]) => {
    if (!category) {
      if (selectedGroup.value && !groups.includes(selectedGroup.value)) {
        selectedGroup.value = null
      }
      return
    }
    if (selectedGroup.value && !groups.includes(selectedGroup.value)) {
      selectedGroup.value = null
    }
  })
  watch(taskOptions, (options) => {
    if (selectedTaskName.value && !options.some((option) => option.taskName === selectedTaskName.value)) {
      selectedTaskName.value = null
    }
  })
  watch([selectedCategory, selectedGroup, selectedTaskName, taskOptions], () => {
    if (!isFileOptionsReady.value) return
    if (!selectedTaskName.value) {
      selectedFile.value = null
      return
    }
    const taskOption = taskOptions.value.find((option) => option.taskName === selectedTaskName.value)
    selectedFile.value = taskOption?.filename || null
  })
  watch(
    [() => route.query.file, files],
    ([routeFile, currentFiles]) => {
      if (typeof routeFile !== 'string') return
      const keyword = getKeywordFromFilename(routeFile)
      const meta = taskMetaByKeyword.value[keyword]
      if (meta) {
        selectedTaskName.value = meta.taskName
        selectedCategory.value = meta.category
        selectedGroup.value = meta.groupName
        selectedFile.value = currentFiles.includes(routeFile) ? routeFile : routeFile
      } else if (currentFiles.includes(routeFile)) {
        selectedFile.value = routeFile
      }
    },
    { immediate: true }
  )
  watch(
    [() => route.query.category, () => route.query.group, () => route.query.task, () => route.query.recommended],
    ([routeCategory, routeGroup, routeTask, routeRecommended]) => {
      if (typeof routeCategory === 'string') selectedCategory.value = routeCategory
      if (typeof routeGroup === 'string') selectedGroup.value = routeGroup
      if (typeof routeTask === 'string') selectedTaskName.value = routeTask
      if (routeRecommended === '1') {
        filters.ai_recommended_only = true
        filters.keyword_recommended_only = false
      }
    },
    { immediate: true }
  )

  onScopeDispose(() => {
    if (readyTimer) {
      clearTimeout(readyTimer)
      readyTimer = null
    }
    if (_filterDebounce) {
      clearTimeout(_filterDebounce)
      _filterDebounce = null
    }
  })

  onMounted(async () => {
    await Promise.all([fetchFiles(), fetchTaskNameMap()])
    // 初始加载结果（全部分类时也展示数据）
    fetchResults()
    fetchInsights()
  })

  return {
    files,
    selectedFile,
    results,
    insights,
    totalItems,
    filters,
    selectedCategory,
    selectedGroup,
    selectedTaskName,
    isLoading,
    error,
    fetchFiles, // Expose to allow manual refresh
    refreshResults,
    exportSelectedResults,
    deleteSelectedFile,
    fileOptions,
    categoryOptions,
    groupOptions,
    taskOptions,
    isFileOptionsReady,
  }
}
