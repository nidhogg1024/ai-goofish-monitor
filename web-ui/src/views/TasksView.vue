<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useTasks } from '@/composables/useTasks'
import type { Task, TaskUpdate } from '@/types/task.d.ts'
import { parseTaskFormDefaults } from '@/lib/taskFormQuery'
import TaskCreateDialog from '@/components/tasks/TaskCreateDialog.vue'
import BatchCreateDialog from '@/components/tasks/BatchCreateDialog.vue'
import TasksTable from '@/components/tasks/TasksTable.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import { listAccounts, type AccountItem } from '@/api/accounts'
import { Button } from '@/components/ui/button'
import Badge from '@/components/ui/badge/Badge.vue'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
const { t } = useI18n()
const router = useRouter()

const {
  tasks,
  isLoading,
  error,
  fetchTasks,
  removeTask,
  updateTask,
  startTask,
  stopTask,
  stoppingTaskIds,
} = useTasks()
const route = useRoute()

// State for dialogs
const isEditDialogOpen = ref(false)
const isCriteriaDialogOpen = ref(false)
const isEditSubmitting = ref(false)
const selectedTask = ref<Task | null>(null)
const criteriaTask = ref<Task | null>(null)
const criteriaDescription = ref('')
const isCriteriaSubmitting = ref(false)
const isDeleteDialogOpen = ref(false)
const taskToDeleteId = ref<number | null>(null)
const accountOptions = ref<AccountItem[]>([])

const taskToDelete = computed(() => {
  if (taskToDeleteId.value === null) return null
  return tasks.value.find((task) => task.id === taskToDeleteId.value) || null
})
const editDefaults = computed(() => parseTaskFormDefaults(route.query))
const routeCategory = computed(() => typeof route.query.category === 'string' ? route.query.category : null)
const routeGroup = computed(() => typeof route.query.group === 'string' ? route.query.group : null)
const routeTaskName = computed(() => typeof route.query.task === 'string' ? route.query.task : null)
const normalizedTasks = computed(() =>
  tasks.value.map((task) => ({
    ...task,
    category: task.category || '未分类',
    group_name: task.group_name || '默认任务组',
  })),
)
const groupedTaskCategories = computed(() => {
  const categoryMap = new Map<string, {
    name: string
    groups: Array<{
      name: string
      tasks: Task[]
      runningCount: number
      enabledCount: number
      budgetMin: number | null
      budgetMax: number | null
    }>
    totalTasks: number
    runningCount: number
  }>()

  for (const task of normalizedTasks.value) {
    const categoryName = task.category || '未分类'
    const groupName = task.group_name || '默认任务组'
    if (!categoryMap.has(categoryName)) {
      categoryMap.set(categoryName, {
        name: categoryName,
        groups: [],
        totalTasks: 0,
        runningCount: 0,
      })
    }
    const category = categoryMap.get(categoryName)!
    let group = category.groups.find((item) => item.name === groupName)
    if (!group) {
      group = {
        name: groupName,
        tasks: [],
        runningCount: 0,
        enabledCount: 0,
        budgetMin: null,
        budgetMax: null,
      }
      category.groups.push(group)
    }
    group.tasks.push(task)
    category.totalTasks += 1
    if (task.is_running) {
      group.runningCount += 1
      category.runningCount += 1
    }
    if (task.enabled) {
      group.enabledCount += 1
    }
    const minPrice = Number(task.min_price)
    const maxPrice = Number(task.max_price)
    if (!Number.isNaN(minPrice)) {
      group.budgetMin = group.budgetMin === null ? minPrice : Math.min(group.budgetMin, minPrice)
    }
    if (!Number.isNaN(maxPrice)) {
      group.budgetMax = group.budgetMax === null ? maxPrice : Math.max(group.budgetMax, maxPrice)
    }
  }

  return Array.from(categoryMap.values()).map((category) => ({
    ...category,
    groups: category.groups.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
  }))
})
const visibleGroupedTaskCategories = computed(() =>
  groupedTaskCategories.value
    .filter((category) => !routeCategory.value || category.name === routeCategory.value)
    .map((category) => ({
      ...category,
      groups: category.groups.filter((group) => !routeGroup.value || group.name === routeGroup.value),
    }))
    .filter((category) => category.groups.length > 0)
)
const groupedStats = computed(() => ({
  categoryCount: visibleGroupedTaskCategories.value.length,
  groupCount: visibleGroupedTaskCategories.value.reduce((sum, category) => sum + category.groups.length, 0),
  taskCount: visibleGroupedTaskCategories.value.reduce(
    (sum, category) => sum + category.groups.reduce((groupSum, group) => groupSum + group.tasks.length, 0),
    0,
  ),
  runningCount: visibleGroupedTaskCategories.value.reduce(
    (sum, category) => sum + category.groups.reduce((groupSum, group) => groupSum + group.runningCount, 0),
    0,
  ),
}))

function handleDeleteTask(taskId: number) {
  taskToDeleteId.value = taskId
  isDeleteDialogOpen.value = true
}

async function handleConfirmDeleteTask() {
  if (!taskToDelete.value) {
    toast({ title: t('tasks.toasts.notFound'), variant: 'destructive' })
    isDeleteDialogOpen.value = false
    return
  }
  try {
    await removeTask(taskToDelete.value.id)
    toast({ title: t('tasks.toasts.deleted') })
  } catch (e) {
    toast({
      title: t('tasks.toasts.deleteFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isDeleteDialogOpen.value = false
    taskToDeleteId.value = null
  }
}

function handleEditTask(task: Task) {
  selectedTask.value = task
  isEditDialogOpen.value = true
}

watch(
  () => [route.query.edit, tasks.value],
  () => {
    const editTaskId = typeof route.query.edit === 'string' ? Number(route.query.edit) : NaN
    if (!Number.isFinite(editTaskId)) return
    const match = tasks.value.find((task) => task.id === editTaskId)
    if (!match) return
    selectedTask.value = match
    isEditDialogOpen.value = true
  },
  { immediate: true }
)

async function handleUpdateTask(data: TaskUpdate) {
  if (!selectedTask.value) return
  isEditSubmitting.value = true
  try {
    await updateTask(selectedTask.value.id, data)
    isEditDialogOpen.value = false
  }
  catch (e) {
    toast({
      title: t('tasks.toasts.updateFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
  finally {
    isEditSubmitting.value = false
  }
}

function handleOpenCriteriaDialog(task: Task) {
  criteriaTask.value = task
  criteriaDescription.value = task.description || ''
  isCriteriaDialogOpen.value = true
}

async function handleRefreshCriteria() {
  if (!criteriaTask.value) return
  if (!criteriaDescription.value.trim()) {
    toast({
      title: t('tasks.toasts.descriptionRequired'),
      description: t('tasks.criteria.descriptionRequired'),
      variant: 'destructive',
    })
    return
  }

  isCriteriaSubmitting.value = true
  try {
    await updateTask(criteriaTask.value.id, { description: criteriaDescription.value })
    isCriteriaDialogOpen.value = false
  } catch (e) {
    toast({
      title: t('tasks.toasts.regenerateFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isCriteriaSubmitting.value = false
  }
}

const isStartingAll = ref(false)

const canStartTask = (task: Task) =>
  task.enabled &&
  !task.is_running &&
  !task.is_queued &&
  task.execution_state !== 'queued' &&
  task.execution_state !== 'running'

const startableTaskCount = computed(() =>
  tasks.value.filter((t) => canStartTask(t)).length,
)

async function handleStartAll() {
  const toStart = tasks.value.filter((t) => canStartTask(t))
  if (!toStart.length) return
  isStartingAll.value = true
  try {
    await Promise.allSettled(toStart.map((t) => startTask(t.id)))
  } finally {
    isStartingAll.value = false
  }
}

async function handleStartTask(taskId: number) {
  try {
    await startTask(taskId)
  } catch (e) {
    toast({
      title: t('tasks.toasts.startFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

async function handleStopTask(taskId: number) {
  try {
    await stopTask(taskId)
  } catch (e) {
    toast({
      title: t('tasks.toasts.stopFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

async function handleToggleEnabled(task: Task, enabled: boolean) {
  const previous = task.enabled
  task.enabled = enabled
  try {
    await updateTask(task.id, { enabled })
  } catch (e) {
    task.enabled = previous
    toast({
      title: t('tasks.toasts.toggleFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

function openTaskResults(task: Task) {
  router.push({
    name: 'Results',
    query: {
      category: task.category || undefined,
      group: task.group_name || undefined,
      task: task.task_name,
    },
  })
}

function openTaskLogs(task: Task) {
  router.push({
    name: 'Logs',
    query: {
      category: task.category || undefined,
      group: task.group_name || undefined,
      taskId: String(task.id),
    },
  })
}

function openGroupResults(category: string, group: string) {
  router.push({
    name: 'Results',
    query: { category, group },
  })
}

function openGroupLogs(category: string, group: string, firstTaskId?: number) {
  router.push({
    name: 'Logs',
    query: {
      category,
      group,
      taskId: firstTaskId !== undefined ? String(firstTaskId) : undefined,
    },
  })
}

async function fetchAccountOptions() {
  try {
    accountOptions.value = await listAccounts()
  } catch (e) {
    toast({
      title: t('tasks.toasts.loadAccountsFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

onMounted(fetchAccountOptions)

function clearTaskScope() {
  router.push({ name: 'Tasks' })
}
</script>

<template>
  <div>
    <div class="flex justify-between items-center mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-800">
          {{ t('tasks.title') }}
        </h1>
        <p class="mt-1 text-sm text-slate-500">
          按分类与任务组管理监控池，避免不同购买目标平铺混杂。
        </p>
      </div>
      <div class="flex gap-2">
        <Button
          v-if="startableTaskCount > 0"
          variant="outline"
          :disabled="isStartingAll"
          @click="handleStartAll"
        >
          {{ isStartingAll ? t('tasks.table.startingAll') : t('tasks.table.startAll', { count: startableTaskCount }) }}
        </Button>
        <BatchCreateDialog @created="fetchTasks" />
        <TaskCreateDialog :account-options="accountOptions" @created="fetchTasks" />
      </div>
    </div>

    <!-- Edit Task Dialog -->
    <Dialog v-model:open="isEditDialogOpen">
      <DialogContent class="sm:max-w-[640px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.editDialog.title', { task: selectedTask?.task_name || "" }) }}</DialogTitle>
        </DialogHeader>
        <TaskForm
          v-if="selectedTask"
          mode="edit"
          :initial-data="selectedTask"
          :account-options="accountOptions"
          :default-values="editDefaults"
          @submit="(data) => handleUpdateTask(data as TaskUpdate)"
        />
        <DialogFooter>
          <Button type="submit" form="task-form" :disabled="isEditSubmitting">
            {{ isEditSubmitting ? t('common.saving') : t('tasks.editDialog.save') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- Refresh Criteria Dialog -->
    <Dialog v-model:open="isCriteriaDialogOpen">
      <DialogContent class="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.criteria.title') }}</DialogTitle>
          <DialogDescription>
            {{ t('tasks.criteria.description') }}
          </DialogDescription>
        </DialogHeader>
        <div class="grid gap-3">
          <label class="text-sm font-medium text-gray-700">{{ t('tasks.form.description') }}</label>
          <Textarea
            v-model="criteriaDescription"
            class="min-h-[140px]"
            :placeholder="t('tasks.form.descriptionPlaceholder')"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" @click="isCriteriaDialogOpen = false">
            {{ t('common.cancel') }}
          </Button>
          <Button :disabled="isCriteriaSubmitting" @click="handleRefreshCriteria">
            {{ isCriteriaSubmitting ? t('tasks.criteria.generating') : t('tasks.criteria.action') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <div v-if="error" class="app-alert-error mb-4" role="alert">
      <strong class="font-bold">{{ t('common.error') }}</strong>
      <span class="block sm:inline">{{ error.message }}</span>
    </div>

    <div
      v-if="routeCategory || routeGroup || routeTaskName"
      class="app-surface mb-6 flex flex-wrap items-center justify-between gap-3 p-4"
    >
      <div>
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">当前范围</div>
        <div class="mt-2 flex flex-wrap gap-2 text-sm">
          <span v-if="routeCategory" class="rounded-full bg-slate-100 px-3 py-1 font-medium text-slate-700">{{ routeCategory }}</span>
          <span v-if="routeGroup" class="rounded-full bg-emerald-50 px-3 py-1 font-medium text-emerald-700">{{ routeGroup }}</span>
          <span v-if="routeTaskName" class="rounded-full bg-blue-50 px-3 py-1 font-medium text-blue-700">{{ routeTaskName }}</span>
        </div>
      </div>
      <Button variant="outline" @click="clearTaskScope">查看全部任务</Button>
    </div>

    <div class="mb-6 grid gap-4 md:grid-cols-4">
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">分类数</div>
        <div class="mt-2 text-2xl font-black text-slate-800">{{ groupedStats.categoryCount }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">任务组</div>
        <div class="mt-2 text-2xl font-black text-slate-800">{{ groupedStats.groupCount }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">任务总数</div>
        <div class="mt-2 text-2xl font-black text-slate-800">{{ groupedStats.taskCount }}</div>
      </div>
      <div class="app-surface p-4">
        <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">运行中</div>
        <div class="mt-2 text-2xl font-black text-emerald-600">{{ groupedStats.runningCount }}</div>
      </div>
    </div>

    <div class="space-y-8">
      <section
        v-for="category in visibleGroupedTaskCategories"
        :key="category.name"
        class="space-y-4"
      >
        <div class="flex items-center justify-between gap-3">
          <div>
            <h2 class="text-xl font-black tracking-tight text-slate-800">{{ category.name }}</h2>
            <p class="mt-1 text-sm text-slate-500">
              {{ category.totalTasks }} 个任务，{{ category.groups.length }} 个任务组，当前运行 {{ category.runningCount }} 个
            </p>
          </div>
          <Badge variant="secondary" class="bg-slate-100 text-slate-700">
            {{ category.groups.length }} 组
          </Badge>
        </div>

        <article
          v-for="group in category.groups"
          :key="`${category.name}-${group.name}`"
          class="app-surface p-4"
        >
          <div class="mb-4 flex flex-col gap-3 border-b border-slate-100 pb-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-lg font-black text-slate-800">{{ group.name }}</h3>
                <Badge variant="outline" class="border-slate-200 bg-slate-50 text-slate-600">
                  {{ category.name }}
                </Badge>
              </div>
              <div class="mt-2 flex flex-wrap gap-4 text-sm text-slate-500">
                <span>{{ group.tasks.length }} 个候选任务</span>
                <span>已启用 {{ group.enabledCount }} 个</span>
                <span>运行中 {{ group.runningCount }} 个</span>
                <span>
                  预算
                  {{
                    group.budgetMin !== null || group.budgetMax !== null
                      ? `¥${group.budgetMin ?? '—'} - ${group.budgetMax ?? '—'}`
                      : '未设置'
                  }}
                </span>
              </div>
            </div>
            <div class="flex flex-wrap gap-2">
              <Button variant="outline" @click="openGroupResults(category.name, group.name)">
                查看情报
              </Button>
              <Button
                variant="outline"
                @click="openGroupLogs(category.name, group.name, group.tasks[0]?.id)"
              >
                查看日志
              </Button>
            </div>
          </div>

          <TasksTable
            :tasks="group.tasks"
            :is-loading="isLoading"
            :stopping-ids="stoppingTaskIds"
            @delete-task="handleDeleteTask"
            @edit-task="handleEditTask"
            @run-task="handleStartTask"
            @stop-task="handleStopTask"
            @refresh-criteria="handleOpenCriteriaDialog"
            @toggle-enabled="handleToggleEnabled"
            @open-results="openTaskResults"
            @open-logs="openTaskLogs"
          />
        </article>
      </section>
    </div>

    <Dialog v-model:open="isDeleteDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.deleteDialog.title') }}</DialogTitle>
          <DialogDescription>
            {{ taskToDelete ? t('tasks.deleteDialog.descriptionWithTask', { task: taskToDelete.task_name }) : t('tasks.deleteDialog.descriptionFallback') }}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isDeleteDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" @click="handleConfirmDeleteTask">{{ t('tasks.deleteDialog.confirm') }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
