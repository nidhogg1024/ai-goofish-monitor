<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { batchGenerate, batchCreateTasks } from '@/api/batchTasks'
import { useBatchGeneration } from '@/composables/useBatchGeneration'
import type { BatchCreateResult, TaskGenerateRequest } from '@/types/task.d.ts'
import TaskGenerationProgress from '@/components/tasks/TaskGenerationProgress.vue'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/components/ui/toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

const { t } = useI18n()

const emit = defineEmits<{
  (event: 'created'): void
}>()

// Dialog state
const isOpen = ref(false)
type Step = 'input' | 'progress' | 'preview' | 'result'
const currentStep = ref<Step>('input')
type PreviewTask = Omit<TaskGenerateRequest, 'task_name' | 'keyword' | 'min_price' | 'max_price' | 'description'> & {
  task_name: string
  keyword: string
  min_price: string
  max_price: string
  description: string
  _selected: boolean
  _expanded: boolean
}

// Input state
const urlContent = ref('')
const textContent = ref('')

// Batch generation
const {
  activeJob,
  pollingError,
  isAnalyzing,
  beginPolling,
  clearJob,
} = useBatchGeneration()

// Preview state
const previews = ref<PreviewTask[]>([])
const selectedCount = computed(() => previews.value.filter((p) => p._selected).length)

// Create result state
const isCreating = ref(false)
const createResults = ref<BatchCreateResult[]>([])

function resetState() {
  currentStep.value = 'input'
  urlContent.value = ''
  textContent.value = ''
  clearJob()
  previews.value = []
  isCreating.value = false
  createResults.value = []
}

function validate(): boolean {
  const url = urlContent.value.trim()
  const text = textContent.value.trim()
  if (!url && !text) {
    toast({ title: t('tasks.batchCreate.validation.contentRequired'), variant: 'destructive' })
    return false
  }
  if (url && !url.startsWith('http://') && !url.startsWith('https://')) {
    toast({ title: t('tasks.batchCreate.validation.urlInvalid'), variant: 'destructive' })
    return false
  }
  return true
}

async function handleStartAnalyze() {
  if (!validate()) return
  clearJob()

  try {
    const result = await batchGenerate({
      url: urlContent.value.trim() || undefined,
      description: textContent.value.trim() || undefined,
    })
    currentStep.value = 'progress'
    beginPolling(result.job)
  } catch (error) {
    toast({
      title: t('tasks.toasts.createFailed'),
      description: (error as Error).message,
      variant: 'destructive',
    })
  }
}

// Watch for job completion
watch(
  () => activeJob.value?.status,
  (status, prev) => {
    if (!status || status === prev) return
    if (status === 'completed' && activeJob.value?.previews?.length) {
      previews.value = activeJob.value.previews.map((p) => ({
        ...p,
        task_name: p.task_name ?? '',
        keyword: p.keyword ?? '',
        min_price: p.min_price ?? '',
        max_price: p.max_price ?? '',
        description: p.description ?? '',
        decision_mode: (p as any).decision_mode || 'ai',
        _selected: true,
        _expanded: false,
      }))
      currentStep.value = 'preview'
    }
    if (status === 'failed') {
      toast({
        title: t('tasks.toasts.createFailed'),
        description: activeJob.value?.error || activeJob.value?.message,
        variant: 'destructive',
      })
    }
  },
)

watch(pollingError, (val) => {
  if (!val) return
  toast({
    title: t('tasks.toasts.progressFailed'),
    description: val.message,
    variant: 'destructive',
  })
})

function toggleSelectAll() {
  const allSelected = previews.value.every((p) => p._selected)
  previews.value.forEach((p) => { p._selected = !allSelected })
}

async function handleBatchCreate() {
  const selected = previews.value.filter((p) => p._selected)
  if (!selected.length) {
    toast({ title: t('tasks.batchCreate.validation.noTaskSelected'), variant: 'destructive' })
    return
  }

  isCreating.value = true
  try {
    const tasksToCreate: TaskGenerateRequest[] = selected.map(({ _selected, _expanded, ...rest }) => ({
      ...rest,
      task_name: rest.task_name?.trim() || null,
      keyword: rest.keyword?.trim() || null,
      min_price: rest.min_price?.trim() || null,
      max_price: rest.max_price?.trim() || null,
      description: rest.description?.trim() || '',
    }))
    const result = await batchCreateTasks(tasksToCreate)
    createResults.value = result.results
    currentStep.value = 'result'
    emit('created')
  } catch (error) {
    toast({
      title: t('tasks.toasts.createFailed'),
      description: (error as Error).message,
      variant: 'destructive',
    })
  } finally {
    isCreating.value = false
  }
}

const resultSummary = computed(() => {
  const total = createResults.value.length
  const success = createResults.value.filter((r) => r.success).length
  const fail = total - success
  return { total, success, fail }
})

function handleBackToInput() {
  currentStep.value = 'input'
  clearJob()
  previews.value = []
}

function handleClose() {
  isOpen.value = false
}

watch(isOpen, (val) => {
  if (!val) resetState()
})

function formatPrice(min: string | null | undefined, max: string | null | undefined) {
  if (min && max) return `${min} - ${max}`
  if (max) return `≤ ${max}`
  if (min) return `≥ ${min}`
  return '-'
}
</script>

<template>
  <Dialog v-model:open="isOpen">
    <DialogTrigger as-child>
      <Button variant="outline">{{ t('tasks.batchCreate.trigger') }}</Button>
    </DialogTrigger>
    <DialogContent class="sm:max-w-[720px] max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{{ t('tasks.batchCreate.title') }}</DialogTitle>
        <DialogDescription>{{ t('tasks.batchCreate.description') }}</DialogDescription>
      </DialogHeader>

      <!-- Step 1: Input -->
      <div v-if="currentStep === 'input'" class="space-y-4">
        <div class="space-y-2">
          <Label class="text-sm font-medium">{{ t('tasks.batchCreate.urlLabel') }}</Label>
          <Input
            v-model="urlContent"
            type="url"
            :placeholder="t('tasks.batchCreate.urlPlaceholder')"
          />
          <p class="text-xs text-slate-500">{{ t('tasks.batchCreate.urlHint') }}</p>
        </div>
        <div class="space-y-2">
          <Label class="text-sm font-medium">{{ t('tasks.batchCreate.descriptionLabel') }}</Label>
          <Textarea
            v-model="textContent"
            class="min-h-[140px]"
            :placeholder="t('tasks.batchCreate.textPlaceholder')"
          />
          <p class="text-xs text-slate-500">{{ t('tasks.batchCreate.descriptionHint') }}</p>
        </div>
      </div>

      <!-- Step 2: Progress -->
      <div v-if="currentStep === 'progress'" class="space-y-3">
        <TaskGenerationProgress
          v-if="activeJob"
          :job="activeJob as any"
        />
        <p v-if="activeJob" class="text-xs text-slate-500">
          {{ t('tasks.batchCreate.progress.helperRunning') }}
        </p>
      </div>

      <!-- Step 3: Preview -->
      <div v-if="currentStep === 'preview'" class="space-y-4">
        <div class="flex items-center justify-between">
          <p class="text-sm font-medium text-slate-700">
            {{ t('tasks.batchCreate.preview.title') }}
            <Badge variant="secondary" class="ml-2">
              {{ t('tasks.batchCreate.preview.taskCount', { count: previews.length }) }}
            </Badge>
          </p>
          <Button variant="ghost" size="sm" @click="toggleSelectAll">
            {{ previews.every((p) => p._selected) ? t('tasks.batchCreate.preview.deselectAll') : t('tasks.batchCreate.preview.selectAll') }}
          </Button>
        </div>

        <div class="space-y-3 max-h-[50vh] overflow-y-auto pr-1">
          <div
            v-for="(preview, index) in previews"
            :key="index"
            class="rounded-xl border bg-white p-3 space-y-2"
            :class="preview._selected ? 'border-slate-300' : 'border-slate-200 opacity-60'"
          >
            <!-- Header row -->
            <div class="flex items-start gap-3">
              <Checkbox
                v-model:checked="preview._selected"
                class="mt-1"
              />
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-semibold text-slate-900 truncate">
                    {{ preview.task_name || '未命名任务' }}
                  </span>
                  <Badge variant="outline" class="shrink-0 text-xs">
                    {{ preview.keyword }}
                  </Badge>
                </div>
                <p v-if="(preview as any).reason" class="mt-1 text-xs text-slate-600">
                  {{ (preview as any).reason }}
                </p>
                <div class="flex gap-3 mt-1 text-xs text-slate-500">
                  <span v-if="preview.max_price || preview.min_price">
                    {{ t('tasks.batchCreate.preview.priceRange') }}: {{ formatPrice(preview.min_price, preview.max_price) }}
                  </span>
                  <span v-if="preview.personal_only">{{ t('tasks.batchCreate.preview.personalOnly') }}</span>
                  <span v-if="preview.free_shipping">{{ t('tasks.batchCreate.preview.freeShipping') }}</span>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                class="shrink-0 text-xs"
                @click="preview._expanded = !preview._expanded"
              >
                {{ preview._expanded ? t('tasks.batchCreate.preview.collapse') : t('tasks.batchCreate.preview.expand') }}
              </Button>
            </div>

            <!-- Expanded edit fields -->
            <div v-if="preview._expanded" class="ml-8 space-y-3 pt-2 border-t border-slate-100">
              <div class="grid sm:grid-cols-2 gap-3">
                <div>
                  <Label class="text-xs">{{ t('tasks.batchCreate.preview.taskName') }}</Label>
                  <Input v-model="preview.task_name" class="mt-1" />
                </div>
                <div>
                  <Label class="text-xs">{{ t('tasks.batchCreate.preview.keyword') }}</Label>
                  <Input v-model="preview.keyword" class="mt-1" />
                </div>
              </div>
              <div class="grid sm:grid-cols-2 gap-3">
                <div>
                  <Label class="text-xs">{{ t('tasks.form.minPrice') }}</Label>
                  <Input v-model="preview.min_price" class="mt-1" />
                </div>
                <div>
                  <Label class="text-xs">{{ t('tasks.form.maxPrice') }}</Label>
                  <Input v-model="preview.max_price" class="mt-1" />
                </div>
              </div>
              <div>
                <Label class="text-xs">{{ t('tasks.batchCreate.preview.description') }}</Label>
                <Textarea v-model="preview.description" class="mt-1 min-h-[80px]" />
              </div>
              <div class="flex gap-4">
                <div class="flex items-center gap-2">
                  <Switch v-model:checked="preview.personal_only" />
                  <Label class="text-xs">{{ t('tasks.batchCreate.preview.personalOnly') }}</Label>
                </div>
                <div class="flex items-center gap-2">
                  <Switch v-model:checked="preview.free_shipping" />
                  <Label class="text-xs">{{ t('tasks.batchCreate.preview.freeShipping') }}</Label>
                </div>
                <div class="flex items-center gap-2">
                  <Switch v-model:checked="preview.analyze_images" />
                  <Label class="text-xs">{{ t('tasks.batchCreate.preview.analyzeImages') }}</Label>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 4: Result -->
      <div v-if="currentStep === 'result'" class="space-y-3">
        <div class="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
          <p class="text-sm font-semibold text-slate-900">
            {{ resultSummary.fail === 0
              ? t('tasks.batchCreate.resultAllSuccess', { count: resultSummary.success })
              : t('tasks.batchCreate.resultPartialFail', { success: resultSummary.success, total: resultSummary.total, fail: resultSummary.fail })
            }}
          </p>
        </div>
        <div class="space-y-2 max-h-[40vh] overflow-y-auto">
          <div
            v-for="(result, index) in createResults"
            :key="index"
            class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
            :class="result.success ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'"
          >
            <span
              class="h-2 w-2 rounded-full shrink-0"
              :class="result.success ? 'bg-emerald-500' : 'bg-red-500'"
            />
            <span class="flex-1 truncate" :class="result.success ? 'text-slate-700' : 'text-red-700'">
              {{ result.success ? (result.task?.task_name || '已创建') : (result.task_name || '未知') }}
            </span>
            <span v-if="!result.success" class="text-xs text-red-500 truncate max-w-[200px]">
              {{ result.error }}
            </span>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <DialogFooter>
        <template v-if="currentStep === 'input'">
          <Button @click="handleStartAnalyze" :disabled="isAnalyzing">
            {{ isAnalyzing ? t('tasks.batchCreate.analyzing') : t('tasks.batchCreate.startAnalyze') }}
          </Button>
        </template>
        <template v-if="currentStep === 'progress'">
          <Button variant="outline" @click="handleBackToInput">
            {{ t('tasks.batchCreate.backToInput') }}
          </Button>
        </template>
        <template v-if="currentStep === 'preview'">
          <Button variant="outline" @click="handleBackToInput">
            {{ t('tasks.batchCreate.backToInput') }}
          </Button>
          <Button @click="handleBatchCreate" :disabled="isCreating || selectedCount === 0">
            {{ isCreating ? t('tasks.batchCreate.creating') : t('tasks.batchCreate.confirm', { count: selectedCount }) }}
          </Button>
        </template>
        <template v-if="currentStep === 'result'">
          <Button variant="outline" @click="handleClose">
            {{ t('tasks.batchCreate.done') }}
          </Button>
        </template>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
