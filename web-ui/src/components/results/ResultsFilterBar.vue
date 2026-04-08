<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'

interface FileOption {
  value: string
  label: string
  taskName?: string
  category?: string
  groupName?: string
}

interface Props {
  files: string[]
  fileOptions?: FileOption[]
  categoryOptions?: string[]
  groupOptions?: string[]
  taskOptions?: FileOption[]
  selectedCategory: string | null
  selectedGroup: string | null
  selectedTaskName: string | null
  selectedFile: string | null
  aiRecommendedOnly: boolean
  keywordRecommendedOnly: boolean
  sortBy: 'crawl_time' | 'publish_time' | 'price' | 'keyword_hit_count'
  sortOrder: 'asc' | 'desc'
  isLoading: boolean
  isReady: boolean
}

const props = defineProps<Props>()
const { t } = useI18n()

const options = computed<FileOption[]>(() => {
  if (!props.isReady) {
    return []
  }
  if (props.fileOptions && props.fileOptions.length > 0) {
    return props.fileOptions
  }
  return props.files.map((file) => ({ value: file, label: file, taskName: file }))
})

const selectedLabel = computed(() => {
  if (!props.isReady) return t('results.filters.loadingTaskNames')
  if (options.value.length === 0) return t('results.filters.noResults')
  if (!props.selectedTaskName) return '全部任务'
  const activeOptions = props.taskOptions && props.taskOptions.length > 0 ? props.taskOptions : options.value
  const match = activeOptions.find((option) => option.taskName === props.selectedTaskName)
  return match ? match.label : t('results.filters.taskNameLabel', { task: t('common.unnamed') })
})

const labelClass = computed(() => {
  const classes = ['transition-opacity', 'duration-200']
  const activeOptions = props.taskOptions && props.taskOptions.length > 0 ? props.taskOptions : options.value
  if (!props.isReady || !props.selectedTaskName || activeOptions.length === 0) {
    classes.push('text-muted-foreground')
  }
  classes.push(props.isReady ? 'opacity-100' : 'opacity-70')
  return classes.join(' ')
})

const isSelectDisabled = computed(() => {
  const activeOptions = props.taskOptions && props.taskOptions.length > 0 ? props.taskOptions : options.value
  return !props.isReady || activeOptions.length === 0
})

const emit = defineEmits<{
  (e: 'update:selectedFile', value: string): void
  (e: 'update:selectedCategory', value: string | null): void
  (e: 'update:selectedGroup', value: string | null): void
  (e: 'update:selectedTaskName', value: string | null): void
  (e: 'update:aiRecommendedOnly', value: boolean): void
  (e: 'update:keywordRecommendedOnly', value: boolean): void
  (e: 'update:sortBy', value: 'crawl_time' | 'publish_time' | 'price' | 'keyword_hit_count'): void
  (e: 'update:sortOrder', value: 'asc' | 'desc'): void
  (e: 'refresh'): void
  (e: 'export'): void
  (e: 'delete'): void
}>()

function handleToggleAiRecommended(value: boolean) {
  emit('update:aiRecommendedOnly', value)
  if (value) {
    emit('update:keywordRecommendedOnly', false)
  }
}

function handleToggleKeywordRecommended(value: boolean) {
  emit('update:keywordRecommendedOnly', value)
  if (value) {
    emit('update:aiRecommendedOnly', false)
  }
}
</script>

<template>
  <div class="app-surface mb-3 px-4 py-3">
    <div class="flex flex-wrap items-center gap-2">
      <Select
        :model-value="props.selectedCategory || '__all__'"
        @update:model-value="(value) => emit('update:selectedCategory', value === '__all__' ? null : value as string)"
      >
        <SelectTrigger class="h-8 w-auto min-w-[100px] max-w-[160px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部分类</SelectItem>
          <SelectItem v-for="category in props.categoryOptions || []" :key="category" :value="category">
            {{ category }}
          </SelectItem>
        </SelectContent>
      </Select>

      <Select
        :model-value="props.selectedGroup || '__all__'"
        @update:model-value="(value) => emit('update:selectedGroup', value === '__all__' ? null : value as string)"
      >
        <SelectTrigger class="h-8 w-auto min-w-[100px] max-w-[160px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部任务组</SelectItem>
          <SelectItem v-for="group in props.groupOptions || []" :key="group" :value="group">
            {{ group }}
          </SelectItem>
        </SelectContent>
      </Select>

      <Select
        :model-value="props.selectedTaskName || '__all__'"
        @update:model-value="(value) => emit('update:selectedTaskName', value === '__all__' ? null : value as string)"
      >
        <SelectTrigger class="h-8 w-auto min-w-[100px] max-w-[180px] text-xs" :disabled="isSelectDisabled">
          <span :class="labelClass">{{ selectedLabel }}</span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部任务</SelectItem>
          <SelectItem v-for="option in props.taskOptions || options" :key="option.value" :value="option.taskName || option.value">
            {{ option.label }}
          </SelectItem>
        </SelectContent>
      </Select>

      <div class="mx-1 hidden h-5 w-px bg-slate-200 sm:block" />

      <Select
        :model-value="props.sortBy"
        @update:model-value="(value) => emit('update:sortBy', value as any)"
      >
        <SelectTrigger class="h-8 w-auto min-w-[90px] max-w-[140px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="crawl_time">{{ t('results.filters.sortByCrawlTime') }}</SelectItem>
          <SelectItem value="publish_time">{{ t('results.filters.sortByPublishTime') }}</SelectItem>
          <SelectItem value="price">{{ t('results.filters.sortByPrice') }}</SelectItem>
          <SelectItem value="keyword_hit_count">{{ t('results.filters.sortByKeywordHits') }}</SelectItem>
        </SelectContent>
      </Select>

      <Select
        :model-value="props.sortOrder"
        @update:model-value="(value) => emit('update:sortOrder', value as any)"
      >
        <SelectTrigger class="h-8 w-auto min-w-[70px] max-w-[100px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="desc">{{ t('results.filters.desc') }}</SelectItem>
          <SelectItem value="asc">{{ t('results.filters.asc') }}</SelectItem>
        </SelectContent>
      </Select>

      <div class="mx-1 hidden h-5 w-px bg-slate-200 sm:block" />

      <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600">
        <Checkbox
          id="ai-recommended-only"
          :model-value="props.aiRecommendedOnly"
          @update:modelValue="(value) => handleToggleAiRecommended(value === true)"
          class="h-3.5 w-3.5"
        />
        {{ t('results.filters.aiOnly') }}
      </label>

      <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600">
        <Checkbox
          id="keyword-recommended-only"
          :model-value="props.keywordRecommendedOnly"
          @update:modelValue="(value) => handleToggleKeywordRecommended(value === true)"
          class="h-3.5 w-3.5"
        />
        {{ t('results.filters.keywordOnly') }}
      </label>

      <div class="ml-auto flex items-center gap-1.5">
        <Button size="sm" class="h-8 px-3 text-xs" @click="emit('refresh')" :disabled="props.isLoading">
          {{ t('common.refresh') }}
        </Button>
        <Button size="sm" variant="outline" class="h-8 px-3 text-xs" @click="emit('export')" :disabled="props.isLoading || !props.selectedFile">
          {{ t('results.filters.exportCsv') }}
        </Button>
        <Button size="sm" variant="destructive" class="h-8 px-3 text-xs" @click="emit('delete')" :disabled="props.isLoading || !props.selectedFile">
          {{ t('results.filters.deleteResult') }}
        </Button>
      </div>
    </div>
  </div>
</template>
