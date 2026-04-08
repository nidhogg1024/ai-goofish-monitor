<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useResults } from '@/composables/useResults'
import ResultsFilterBar from '@/components/results/ResultsFilterBar.vue'
import ResultsGrid from '@/components/results/ResultsGrid.vue'
import ResultsInsightsPanel from '@/components/results/ResultsInsightsPanel.vue'
import { Button } from '@/components/ui/button'
import { toast } from '@/components/ui/toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'

const { t } = useI18n()

const {
  files,
  selectedFile,
  results,
  insights,
  totalItems,
  filters,
  isLoading,
  error,
  refreshResults,
  exportSelectedResults,
  deleteSelectedFile,
  fileOptions,
  categoryOptions,
  groupOptions,
  taskOptions,
  selectedCategory,
  selectedGroup,
  selectedTaskName,
  isFileOptionsReady,
} = useResults()

const isDeleteDialogOpen = ref(false)
const insightsExpanded = ref(false)

const selectedTaskLabel = computed(() => {
  if (selectedTaskName.value) return selectedTaskName.value
  if (!selectedFile.value || fileOptions.value.length === 0) return null
  const match = fileOptions.value.find((option) => option.value === selectedFile.value)
  if (!match) return null
  return match.taskName || null
})

const deleteConfirmText = computed(() => {
  return selectedTaskLabel.value
    ? t('results.filters.deleteDialogWithTask', { task: selectedTaskLabel.value })
    : t('results.filters.deleteDialogFallback')
})

function openDeleteDialog() {
  if (!selectedFile.value) {
    toast({
      title: t('results.filters.noResultToDelete'),
      variant: 'destructive',
    })
    return
  }
  isDeleteDialogOpen.value = true
}

function handleExportResults() {
  if (!selectedFile.value) {
    toast({
      title: t('results.filters.noResultToExport'),
      variant: 'destructive',
    })
    return
  }
  exportSelectedResults()
}

async function handleDeleteResults() {
  if (!selectedFile.value) return
  try {
    await deleteSelectedFile(selectedFile.value)
    toast({ title: t('results.filters.resultDeleted') })
  } catch (e) {
    toast({
      title: t('results.filters.deleteFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isDeleteDialogOpen.value = false
  }
}
</script>

<template>
  <div>
    <div class="mb-3 flex items-center justify-between">
      <h1 class="text-lg font-bold text-gray-800">
        {{ t('results.title') }}
        <span v-if="totalItems > 0" class="ml-2 text-sm font-normal text-slate-400">
          {{ totalItems }} 条结果
        </span>
      </h1>
      <button
        type="button"
        class="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
        @click="insightsExpanded = !insightsExpanded"
      >
        价格洞察
        <component :is="insightsExpanded ? ChevronUp : ChevronDown" class="h-3.5 w-3.5" />
      </button>
    </div>

    <div v-if="error" class="app-alert-error mb-3" role="alert">
      <strong class="font-bold">{{ t('common.error') }}</strong>
      <span class="block sm:inline">{{ error.message }}</span>
    </div>

    <ResultsFilterBar
      :files="files"
      :file-options="fileOptions"
      :category-options="categoryOptions"
      :group-options="groupOptions"
      :task-options="taskOptions"
      v-model:selectedCategory="selectedCategory"
      v-model:selectedGroup="selectedGroup"
      v-model:selectedTaskName="selectedTaskName"
      :is-ready="isFileOptionsReady"
      v-model:selectedFile="selectedFile"
      v-model:aiRecommendedOnly="filters.ai_recommended_only"
      v-model:keywordRecommendedOnly="filters.keyword_recommended_only"
      v-model:sortBy="filters.sort_by"
      v-model:sortOrder="filters.sort_order"
      :is-loading="isLoading"
      @refresh="refreshResults"
      @export="handleExportResults"
      @delete="openDeleteDialog"
    />

    <Transition
      enter-active-class="transition-all duration-300 ease-out"
      enter-from-class="opacity-0 max-h-0 overflow-hidden"
      enter-to-class="opacity-100 max-h-[800px]"
      leave-active-class="transition-all duration-200 ease-in"
      leave-from-class="opacity-100 max-h-[800px]"
      leave-to-class="opacity-0 max-h-0 overflow-hidden"
    >
      <ResultsInsightsPanel v-if="insightsExpanded" :insights="insights" :selected-task-label="selectedTaskLabel" />
    </Transition>

    <ResultsGrid :results="results" :is-loading="isLoading" />

    <Dialog v-model:open="isDeleteDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('results.filters.deleteDialogTitle') }}</DialogTitle>
          <DialogDescription>
            {{ deleteConfirmText }}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isDeleteDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" :disabled="isLoading" @click="handleDeleteResults">
            {{ t('results.filters.confirmDelete') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
