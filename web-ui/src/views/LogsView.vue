<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useLogs } from '@/composables/useLogs'
import { useTasks } from '@/composables/useTasks'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { tasks } = useTasks()
const { logs, isAutoRefresh, clearLogs, toggleAutoRefresh, fetchLogs, setScope, loadLatest, loadPrevious, isFetchingHistory, hasMoreHistory } = useLogs()
const logContainer = ref<HTMLElement | null>(null)
const autoScroll = ref(true)
const isClearDialogOpen = ref(false)
const selectedTaskId = ref('')
const selectedCategory = ref<string | null>(null)
const selectedGroup = ref<string | null>(null)
const isPrepending = ref(false)
const lastScrollTop = ref(0)
const lastScrollHeight = ref(0)

const normalizedTasks = computed(() =>
  tasks.value.map((task) => ({
    ...task,
    category: task.category || '未分类',
    group_name: task.group_name || '默认任务组',
  })),
)

const categoryOptions = computed(() =>
  Array.from(new Set(normalizedTasks.value.map((task) => task.category))).sort((a, b) =>
    a.localeCompare(b, 'zh-CN')
  ),
)

const groupOptions = computed(() => {
  const currentCategory = selectedCategory.value
  const groups = normalizedTasks.value
    .filter((task) => !currentCategory || task.category === currentCategory)
    .map((task) => task.group_name)
  return Array.from(new Set(groups)).sort((a, b) => a.localeCompare(b, 'zh-CN'))
})

const filteredTasks = computed(() =>
  normalizedTasks.value.filter((task) => {
    const categoryMatch = !selectedCategory.value || task.category === selectedCategory.value
    const groupMatch = !selectedGroup.value || task.group_name === selectedGroup.value
    return categoryMatch && groupMatch
  }),
)

const activeTask = computed(() =>
  filteredTasks.value.find((task) => String(task.id) === selectedTaskId.value) || null,
)

const scopedTaskIds = computed(() =>
  activeTask.value ? [activeTask.value.id] : filteredTasks.value.map((task) => task.id),
)

const logContext = computed(() => ({
  category: selectedCategory.value || activeTask.value?.category || '全部分类',
  group: selectedGroup.value || activeTask.value?.group_name || '全部任务组',
  taskName: activeTask.value?.task_name || (filteredTasks.value.length ? '整组运行日志' : '未选择任务'),
  runningCount: filteredTasks.value.filter((task) => task.is_running).length,
  taskCount: filteredTasks.value.length,
}))

// Auto-scroll logic
watch(logs, async () => {
  if (isPrepending.value) {
    await nextTick()
    if (logContainer.value) {
      const delta = logContainer.value.scrollHeight - lastScrollHeight.value
      logContainer.value.scrollTop = lastScrollTop.value + delta
    }
    isPrepending.value = false
    return
  }
  if (autoScroll.value) {
    await nextTick()
    scrollToBottom()
  }
})

watch(
  () => [route.query.category, route.query.group, route.query.taskId, route.query.task] as const,
  ([category, group, taskId, taskName]) => {
    selectedCategory.value = typeof category === 'string' ? category : null
    selectedGroup.value = typeof group === 'string' ? group : null
    if (typeof taskId === 'string') {
      selectedTaskId.value = taskId
      return
    }
    if (typeof taskName === 'string') {
      const match = normalizedTasks.value.find((task) => task.task_name === taskName)
      if (match) {
        selectedTaskId.value = String(match.id)
      }
    }
  },
  { immediate: true }
)

watch(
  [normalizedTasks, () => route.query.task],
  ([list, routeTask]) => {
    if (selectedTaskId.value || typeof routeTask !== 'string') return
    const match = list.find((task) => task.task_name === routeTask)
    if (match) {
      selectedTaskId.value = String(match.id)
    }
  },
  { immediate: true }
)

watch([selectedCategory, groupOptions], ([, groups]) => {
  if (selectedGroup.value && !groups.includes(selectedGroup.value)) {
    selectedGroup.value = null
  }
})

watch(filteredTasks, (list) => {
  if (!list.length) {
    selectedTaskId.value = ''
    return
  }
  if (selectedTaskId.value && !list.some((task) => String(task.id) === selectedTaskId.value)) {
    selectedTaskId.value = ''
  }
}, { immediate: true })

watch([selectedTaskId, scopedTaskIds], ([taskId, taskIds]) => {
  const resolvedTaskId = taskId ? Number(taskId) : null
  const fallbackTaskIds = resolvedTaskId ? [] : taskIds
  setScope(resolvedTaskId, fallbackTaskIds)
  if (resolvedTaskId || fallbackTaskIds.length > 0) {
    loadLatest(50)
  }
}, { immediate: true })

function openTasksView() {
  router.push({
    name: 'Tasks',
    query: {
      category: selectedCategory.value || undefined,
      group: selectedGroup.value || undefined,
      task: activeTask.value?.task_name || undefined,
    },
  })
}

function openResultsView() {
  router.push({
    name: 'Results',
    query: {
      category: selectedCategory.value || undefined,
      group: selectedGroup.value || undefined,
      task: activeTask.value?.task_name || undefined,
    },
  })
}

function scrollToBottom() {
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}

async function handleScroll() {
  if (!logContainer.value) return
  if (!hasMoreHistory.value || isFetchingHistory.value) return
  if (logContainer.value.scrollTop > 120) return
  lastScrollTop.value = logContainer.value.scrollTop
  lastScrollHeight.value = logContainer.value.scrollHeight
  isPrepending.value = true
  await loadPrevious(50)
}

function openClearDialog() {
  isClearDialogOpen.value = true
}

async function handleClearLogs() {
  try {
    await clearLogs()
    toast({ title: t('logs.logsCleared') })
  } catch (e) {
    toast({
      title: t('logs.clearFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isClearDialogOpen.value = false
  }
}
</script>

<template>
  <div class="flex h-[calc(100vh-100px)] flex-col gap-4">
    <div class="app-surface p-4">
      <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div class="flex flex-col gap-4">
          <div>
            <h1 class="text-2xl font-bold text-gray-800">{{ t('logs.title') }}</h1>
            <p class="mt-1 text-sm text-slate-500">
              运行中心按分类、任务组和任务收拢日志，避免故障排查时来回跳页。
            </p>
          </div>

          <div class="grid gap-3 lg:grid-cols-3">
            <div class="flex flex-col gap-2">
              <Label class="text-sm text-gray-600">分类</Label>
              <Select
                :model-value="selectedCategory || '__all__'"
                @update:model-value="(value) => selectedCategory = value === '__all__' ? null : value as string"
              >
                <SelectTrigger class="w-full">
                  <SelectValue placeholder="全部分类" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部分类</SelectItem>
                  <SelectItem v-for="category in categoryOptions" :key="category" :value="category">
                    {{ category }}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div class="flex flex-col gap-2">
              <Label class="text-sm text-gray-600">任务组</Label>
              <Select
                :model-value="selectedGroup || '__all__'"
                @update:model-value="(value) => selectedGroup = value === '__all__' ? null : value as string"
              >
                <SelectTrigger class="w-full">
                  <SelectValue placeholder="全部任务组" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部任务组</SelectItem>
                  <SelectItem v-for="group in groupOptions" :key="group" :value="group">
                    {{ group }}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div class="flex flex-col gap-2">
              <Label class="text-sm text-gray-600">{{ t('logs.task') }}</Label>
              <Select v-model="selectedTaskId">
                <SelectTrigger class="w-full">
                  <SelectValue :placeholder="t('logs.selectTask')" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem v-for="task in filteredTasks" :key="task.id" :value="String(task.id)">
                    {{ task.task_name }}{{ task.is_running ? t('logs.taskRunningSuffix') : '' }}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
      
      <div class="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center md:justify-end">
        <Button variant="outline" size="sm" :disabled="!selectedTaskId" @click="fetchLogs">
          {{ t('common.refresh') }}
        </Button>

        <div class="flex items-center space-x-2">
          <Switch id="auto-refresh" :model-value="isAutoRefresh" @update:model-value="toggleAutoRefresh" />
          <Label for="auto-refresh">{{ t('logs.autoRefresh') }}</Label>
        </div>

        <div class="flex items-center space-x-2">
          <Switch id="auto-scroll" v-model="autoScroll" />
          <Label for="auto-scroll">{{ t('logs.autoScroll') }}</Label>
        </div>

        <Button variant="destructive" size="sm" :disabled="!selectedTaskId" @click="openClearDialog">
          {{ t('logs.clearLogs') }}
        </Button>
      </div>
    </div>
    </div>

    <div class="grid gap-4 md:grid-cols-4">
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">分类</div>
        <div class="mt-2 text-lg font-black text-slate-800">{{ logContext.category }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">任务组</div>
        <div class="mt-2 text-lg font-black text-slate-800">{{ logContext.group }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">组内任务</div>
        <div class="mt-2 text-2xl font-black text-slate-800">{{ logContext.taskCount }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">运行中</div>
        <div class="mt-2 text-2xl font-black text-emerald-600">{{ logContext.runningCount }}</div>
      </div>
    </div>

    <div class="app-surface flex flex-wrap items-center justify-between gap-3 p-4">
      <div>
        <div class="text-sm font-semibold text-slate-800">{{ logContext.taskName }}</div>
        <div class="mt-1 text-sm text-slate-500">
          从这里直接回到任务配置，或者查看对应任务的结果情报。
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <Button variant="outline" @click="openTasksView">回到任务组</Button>
        <Button variant="outline" :disabled="!selectedTaskId" @click="openResultsView">查看任务情报</Button>
      </div>
    </div>

    <Card class="app-surface flex flex-1 flex-col overflow-hidden border-none">
      <CardContent class="flex-1 p-0 relative">
        <pre
          ref="logContainer"
          @scroll="handleScroll"
          class="absolute inset-0 p-4 bg-gray-950 text-gray-100 font-mono text-sm overflow-auto whitespace-pre-wrap break-all"
        >{{ logs }}</pre>
      </CardContent>
    </Card>

    <Dialog v-model:open="isClearDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('logs.dialogTitle') }}</DialogTitle>
          <DialogDescription>
            {{ t('logs.dialogDescription') }}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isClearDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" @click="handleClearLogs">{{ t('logs.confirmClear') }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
