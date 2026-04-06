<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSettings } from '@/composables/useSettings'
import type { NotificationSettingsUpdate, NotificationTestResponse } from '@/api/settings'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'
import { getPromptContent, listPrompts, updatePrompt } from '@/api/prompts'
import NotificationSettingsPanel from '@/components/settings/NotificationSettingsPanel.vue'
import RotationSettingsPanel from '@/components/settings/RotationSettingsPanel.vue'
const { t } = useI18n()

const {
  notificationSettings,
  aiSettings,
  aiModelOptions,
  aiModelListSource,
  aiModelChecks,
  rotationSettings,
  systemStatus,
  isLoading,
  isSaving,
  isLoadingAiModels,
  isProbingAiModels,
  isReady,
  error,
  refreshStatus,
  loadAiModels,
  probeAiModels,
  saveNotificationSettings,
  testNotification,
  saveAiSettings,
  saveRotationSettings,
  testAiConnection
} = useSettings()

const CUSTOM_MODEL_VALUE = '__custom__'

const activeTab = ref('ai')
const route = useRoute()
const validTabs = new Set(['notifications', 'ai', 'rotation', 'status', 'prompts'])

const promptFiles = ref<string[]>([])
const selectedPrompt = ref<string | null>(null)
const promptContent = ref('')
const isPromptLoading = ref(false)
const isPromptSaving = ref(false)
const promptError = ref<string | null>(null)

function notifySuccess(title: string, description?: string) {
  toast({ title, description })
}

function notifyError(title: string, description?: string) {
  toast({ title, description, variant: 'destructive' })
}

async function handleSaveNotifications(payload: NotificationSettingsUpdate) {
  try {
    await saveNotificationSettings(payload)
    notifySuccess(t('settings.notifications.saved'))
  } catch (e) {
    notifyError(t('settings.notifications.saveFailed'), (e as Error).message)
  }
}

async function handleTestNotification(payload: {
  channel?: string
  settings: NotificationSettingsUpdate
}): Promise<NotificationTestResponse> {
  try {
    const result = await testNotification(payload)
    return result
  } catch (e) {
    notifyError(t('settings.notifications.testFailed'), (e as Error).message)
    throw e
  }
}

async function handleSaveAi() {
  try {
    await saveAiSettings()
    notifySuccess(t('settings.ai.saved'))
  } catch (e) {
    notifyError(t('settings.ai.saveFailed'), (e as Error).message)
  }
}

async function handleSaveRotation() {
  try {
    await saveRotationSettings()
    notifySuccess(t('settings.rotation.saved'))
  } catch (e) {
    notifyError(t('settings.rotation.saveFailed'), (e as Error).message)
  }
}

async function handleTestAi() {
  try {
    const res = await testAiConnection()
    notifySuccess(t('settings.ai.testSuccess'), res.message)
  } catch (e) {
    notifyError(t('settings.ai.testFailed'), (e as Error).message)
  }
}

const availableAiModelItems = computed(() => aiModelChecks.value.filter((item) => item.available))
const unavailableAiModelItems = computed(() => aiModelChecks.value.filter((item) => !item.available))
const availableAiModels = computed(() => availableAiModelItems.value.map((item) => item.model))
const unavailableAiModelCount = computed(() => aiModelChecks.value.filter((item) => !item.available).length)
const displayedAiModels = computed(() => (
  availableAiModels.value.length > 0 ? availableAiModels.value : aiModelOptions.value
))
const currentAiModelProbe = computed(() => {
  const currentModel = (aiSettings.value.OPENAI_MODEL_NAME || '').trim()
  if (!currentModel) {
    return null
  }
  return aiModelChecks.value.find((item) => item.model === currentModel) || null
})

const selectedAiModelValue = computed(() => {
  const currentModel = (aiSettings.value.OPENAI_MODEL_NAME || '').trim()
  if (!currentModel) {
    return undefined
  }
  return displayedAiModels.value.includes(currentModel) ? currentModel : CUSTOM_MODEL_VALUE
})

const showManualModelInput = computed(() => {
  const currentModel = (aiSettings.value.OPENAI_MODEL_NAME || '').trim()
  return displayedAiModels.value.length === 0 || !currentModel || !displayedAiModels.value.includes(currentModel)
})

async function handleRefreshAiModels() {
  try {
    const res = await loadAiModels(aiSettings.value)
    const probe = await probeAiModels(res.models, aiSettings.value, true)
    const availableCount = probe.items.filter((item) => item.available).length
    notifySuccess(
      t('settings.ai.modelsLoaded'),
      t('settings.ai.probeSummary', { available: availableCount, total: probe.items.length }),
    )
  } catch (e) {
    notifyError(t('settings.ai.loadModelsFailed'), (e as Error).message)
  }
}

function handleAiModelSelect(value: string) {
  if (value === CUSTOM_MODEL_VALUE) {
    return
  }
  aiSettings.value.OPENAI_MODEL_NAME = value
}

async function fetchPrompts() {
  isPromptLoading.value = true
  promptError.value = null
  try {
    const files = await listPrompts()
    promptFiles.value = files

    if (selectedPrompt.value && files.includes(selectedPrompt.value)) {
      return
    }

    const lastSelected = localStorage.getItem('lastSelectedPrompt')
    if (lastSelected && files.includes(lastSelected)) {
      selectedPrompt.value = lastSelected
      return
    }

    selectedPrompt.value = files[0] || null
  } catch (e) {
    promptError.value = (e as Error).message || t('settings.prompts.promptListFailed')
  } finally {
    isPromptLoading.value = false
  }
}

async function handleSavePrompt() {
  if (!selectedPrompt.value) {
    notifyError(t('settings.prompts.selectPromptFile'))
    return
  }
  isPromptSaving.value = true
  try {
    const res = await updatePrompt(selectedPrompt.value, promptContent.value)
    notifySuccess(t('settings.prompts.saveSuccess'), res.message)
  } catch (e) {
    notifyError(t('settings.prompts.saveFailed'), (e as Error).message)
  } finally {
    isPromptSaving.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'prompts') {
    fetchPrompts()
  }
})

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && validTabs.has(tab)) {
      activeTab.value = tab
    }
  },
  { immediate: true }
)

watch(selectedPrompt, async (value) => {
  if (!value) {
    promptContent.value = ''
    return
  }
  localStorage.setItem('lastSelectedPrompt', value)
  isPromptLoading.value = true
  promptError.value = null
  try {
    const data = await getPromptContent(value)
    promptContent.value = data.content
  } catch (e) {
    promptError.value = (e as Error).message || t('settings.prompts.promptContentFailed')
  } finally {
    isPromptLoading.value = false
  }
})
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold text-gray-800 mb-6">{{ t('settings.title') }}</h1>
    
    <div v-if="error" class="app-alert-error mb-4" role="alert">
      {{ error.message }}
    </div>

    <Tabs v-model="activeTab" class="w-full">
      <TabsList class="mb-4 flex w-full flex-nowrap justify-start gap-1 overflow-x-auto rounded-xl bg-slate-100 p-1">
        <TabsTrigger class="shrink-0" value="ai">{{ t('settings.tabs.ai') }}</TabsTrigger>
        <TabsTrigger class="shrink-0" value="rotation">{{ t('settings.tabs.rotation') }}</TabsTrigger>
        <TabsTrigger class="shrink-0" value="notifications">{{ t('settings.tabs.notifications') }}</TabsTrigger>
        <TabsTrigger class="shrink-0" value="status">{{ t('settings.tabs.status') }}</TabsTrigger>
        <TabsTrigger class="shrink-0" value="prompts">{{ t('settings.tabs.prompts') }}</TabsTrigger>
      </TabsList>

      <!-- AI Tab -->
      <TabsContent value="ai">
        <Card>
          <CardHeader>
            <CardTitle>{{ t('settings.ai.title') }}</CardTitle>
            <CardDescription>{{ t('settings.ai.description') }}</CardDescription>
          </CardHeader>
          <CardContent v-if="isReady" class="space-y-4">
            <div class="grid gap-2">
              <Label>API Base URL</Label>
              <Input v-model="aiSettings.OPENAI_BASE_URL" placeholder="https://api.openai.com/v1" />
            </div>
            <div class="grid gap-2">
              <Label>API Key</Label>
              <Input
                v-model="aiSettings.OPENAI_API_KEY"
                type="password"
                :placeholder="t('settings.ai.keyPlaceholder')"
              />
              <p class="text-xs text-gray-500">
                {{ systemStatus?.env_file.openai_api_key_set ? t('settings.ai.keyConfigured') : t('settings.ai.keyMissing') }}
              </p>
            </div>
            <div class="grid gap-2">
              <Label>{{ t('settings.ai.modelName') }}</Label>
              <div class="flex flex-col gap-2 md:flex-row md:items-center">
                <div class="flex-1">
                  <Select
                    :model-value="selectedAiModelValue"
                    @update:model-value="(value) => handleAiModelSelect(value as string)"
                    :disabled="(isLoadingAiModels || isProbingAiModels) || displayedAiModels.length === 0"
                  >
                    <SelectTrigger class="w-full">
                      <SelectValue :placeholder="t('settings.ai.modelSelectPlaceholder')" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup v-if="availableAiModelItems.length > 0">
                        <SelectLabel>{{ t('settings.ai.availableModelsGroup') }}</SelectLabel>
                        <SelectItem
                          v-for="item in availableAiModelItems"
                          :key="item.model"
                          :value="item.model"
                        >
                          {{ item.model }}
                        </SelectItem>
                      </SelectGroup>
                      <template v-else>
                        <SelectItem
                          v-for="model in displayedAiModels"
                          :key="model"
                          :value="model"
                        >
                          {{ model }}
                        </SelectItem>
                      </template>
                      <template v-if="unavailableAiModelItems.length > 0">
                        <SelectSeparator />
                        <SelectGroup>
                          <SelectLabel>{{ t('settings.ai.unavailableModelsGroup') }}</SelectLabel>
                          <SelectItem
                            v-for="item in unavailableAiModelItems"
                            :key="item.model"
                            :value="`unavailable:${item.model}`"
                            disabled
                          >
                            {{ item.model }}
                          </SelectItem>
                        </SelectGroup>
                      </template>
                      <SelectSeparator />
                      <SelectItem value="__custom__">
                        {{ t('settings.ai.manualEntryOption') }}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  variant="outline"
                  type="button"
                  @click="handleRefreshAiModels"
                  :disabled="isLoadingAiModels || isProbingAiModels || isSaving"
                >
                  {{ (isLoadingAiModels || isProbingAiModels) ? t('settings.ai.loadingModels') : t('settings.ai.refreshModels') }}
                </Button>
              </div>
              <p class="text-xs text-gray-500">
                {{
                  aiModelListSource
                    ? t('settings.ai.modelSource', { source: aiModelListSource })
                    : t('settings.ai.modelListHint')
                }}
              </p>
              <p v-if="aiModelChecks.length" class="text-xs text-gray-500">
                {{
                  t('settings.ai.probeSummary', {
                    available: availableAiModels.length,
                    total: aiModelChecks.length,
                  })
                }}
                <span v-if="unavailableAiModelCount > 0">
                  · {{ t('settings.ai.unavailableSummary', { count: unavailableAiModelCount }) }}
                </span>
              </p>
              <div v-if="showManualModelInput" class="grid gap-2">
                <Label>{{ t('settings.ai.manualModelName') }}</Label>
                <Input
                  v-model="aiSettings.OPENAI_MODEL_NAME"
                  :placeholder="t('settings.ai.manualModelPlaceholder')"
                />
                <p
                  v-if="currentAiModelProbe && !currentAiModelProbe.available"
                  class="text-xs text-amber-600"
                >
                  {{
                    t('settings.ai.currentModelUnavailable', {
                      model: currentAiModelProbe.model,
                      reason: currentAiModelProbe.message,
                    })
                  }}
                </p>
              </div>
            </div>
            <div class="grid gap-2">
              <Label>{{ t('settings.ai.proxy') }}</Label>
              <Input v-model="aiSettings.PROXY_URL" placeholder="http://127.0.0.1:7890" />
            </div>
          </CardContent>
          <CardContent v-else class="py-8 text-sm text-gray-500">
            {{ t('settings.ai.loading') }}
          </CardContent>
          <CardFooter v-if="isReady" class="flex gap-2">
            <Button variant="outline" @click="handleTestAi" :disabled="isSaving">{{ t('settings.ai.testConnection') }}</Button>
            <Button @click="handleSaveAi" :disabled="isSaving">{{ t('settings.ai.save') }}</Button>
          </CardFooter>
        </Card>
      </TabsContent>

      <!-- Rotation Tab -->
      <TabsContent value="rotation">
        <RotationSettingsPanel
          :settings="rotationSettings"
          :is-ready="isReady"
          :is-saving="isSaving"
          @save="handleSaveRotation"
        />
      </TabsContent>

      <!-- Notifications Tab -->
      <TabsContent value="notifications">
        <NotificationSettingsPanel
          :settings="notificationSettings"
          :is-ready="isReady"
          :is-saving="isSaving"
          :save-settings="handleSaveNotifications"
          :test-settings="handleTestNotification"
        />
      </TabsContent>

      <!-- Status Tab -->
      <TabsContent value="status">
        <Card>
          <CardHeader>
            <CardTitle>{{ t('settings.status.title') }}</CardTitle>
            <div class="flex justify-end">
                <Button variant="outline" size="sm" @click="refreshStatus" :disabled="isLoading">{{ t('settings.status.refresh') }}</Button>
            </div>
          </CardHeader>
          <CardContent>
            <div v-if="systemStatus" class="space-y-6">
              <!-- Scraper Process Status -->
              <div class="flex items-center justify-between border-b pb-4">
                <div>
                  <h3 class="font-medium">{{ t('settings.status.scraper') }}</h3>
                  <p class="text-sm text-gray-500">{{ t('settings.status.scraperDescription') }}</p>
                </div>
                <span :class="systemStatus.scraper_running ? 'text-green-600 font-bold bg-green-50 px-3 py-1 rounded-full' : 'text-gray-500 bg-gray-100 px-3 py-1 rounded-full'">
                  {{ systemStatus.scraper_running ? t('common.running') : t('common.idle') }}
                </span>
              </div>

              <!-- Env Config Status -->
              <div>
                <div class="flex items-center justify-between mb-4">
                    <div>
                        <h3 class="font-medium">{{ t('settings.status.env') }}</h3>
                        <p class="text-sm text-gray-500">{{ t('settings.status.envDescription') }}</p>
                    </div>
                    <span :class="systemStatus.env_file.exists ? 'text-green-600 font-bold bg-green-50 px-3 py-1 rounded-full' : 'text-red-600 font-bold bg-red-50 px-3 py-1 rounded-full'">
                        {{ systemStatus.env_file.exists ? t('settings.status.loaded') : t('settings.status.missing') }}
                    </span>
                </div>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="p-3 border rounded-lg" :class="systemStatus.env_file.openai_api_key_set ? 'bg-green-50 border-green-200' : 'bg-yellow-50 border-yellow-200'">
                        <div class="flex justify-between items-center">
                            <span class="font-medium text-sm">OpenAI API Key</span>
                            <span class="text-xs font-bold" :class="systemStatus.env_file.openai_api_key_set ? 'text-green-700' : 'text-yellow-700'">
                                {{ systemStatus.env_file.openai_api_key_set ? t('common.active') : t('common.inactive') }}
                            </span>
                        </div>
                    </div>
                    
                    <div class="p-3 border rounded-lg" :class="systemStatus.configured_notification_channels?.length ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200'">
                         <div class="flex justify-between items-center">
                            <span class="font-medium text-sm">{{ t('settings.status.channels') }}</span>
                             <span class="text-xs font-bold" :class="systemStatus.configured_notification_channels?.length ? 'text-green-700' : 'text-gray-500'">
                                {{ systemStatus.configured_notification_channels?.length ? t('common.active') : t('common.inactive') }}
                            </span>
                        </div>
                         <div class="text-xs text-gray-500 mt-1">
                            {{ systemStatus.configured_notification_channels?.join(', ') || t('settings.status.none') }}
                        </div>
                    </div>
                </div>
              </div>
            </div>
            <div v-else class="text-center py-8 text-gray-500">
                {{ t('settings.status.fetching') }}
            </div>
          </CardContent>
        </Card>
      </TabsContent>

      <!-- Prompt Tab -->
      <TabsContent value="prompts">
        <Card>
          <CardHeader>
            <CardTitle>{{ t('settings.prompts.title') }}</CardTitle>
            <CardDescription>{{ t('settings.prompts.description') }}</CardDescription>
          </CardHeader>
          <CardContent class="space-y-4">
            <div v-if="promptError" class="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded">
              {{ promptError }}
            </div>

            <div class="grid gap-2">
              <Label>{{ t('settings.prompts.selectFile') }}</Label>
              <Select
                :model-value="selectedPrompt || undefined"
                @update:model-value="(value) => selectedPrompt = value as string"
              >
                <SelectTrigger>
                  <SelectValue :placeholder="t('settings.prompts.placeholder')" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem v-for="file in promptFiles" :key="file" :value="file">
                    {{ file }}
                  </SelectItem>
                </SelectContent>
              </Select>
              <p v-if="!promptFiles.length && !isPromptLoading" class="text-sm text-gray-500">
                {{ t('settings.prompts.none') }}
              </p>
            </div>

            <div class="grid gap-2">
              <Label>{{ t('settings.prompts.content') }}</Label>
              <Textarea
                v-model="promptContent"
                class="min-h-[240px]"
                :disabled="!selectedPrompt || isPromptLoading"
                :placeholder="t('settings.prompts.contentPlaceholder')"
              />
            </div>
          </CardContent>
          <CardFooter>
            <Button :disabled="isPromptSaving || !selectedPrompt" @click="handleSavePrompt">
              {{ isPromptSaving ? t('common.saving') : t('settings.prompts.save') }}
            </Button>
          </CardFooter>
        </Card>
      </TabsContent>
    </Tabs>
  </div>
</template>
