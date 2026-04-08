import { ref, onMounted } from 'vue'
import * as settingsApi from '@/api/settings'
import type {
  NotificationSettings,
  NotificationSettingsUpdate,
  NotificationTestResponse,
  AiSettings,
  AiModelListRequest,
  AiModelProbeItem,
  RotationSettings,
  SystemStatus
} from '@/api/settings'

const PROBE_CACHE_KEY = 'ai_model_probe_cache'
const MODELS_CACHE_KEY = 'ai_model_list_cache'
const CACHE_TTL_MS = 24 * 60 * 60 * 1000

function loadCachedModels(): { models: string[]; source: string } | null {
  try {
    const raw = localStorage.getItem(MODELS_CACHE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    if (Date.now() - (data.ts || 0) > CACHE_TTL_MS) return null
    return { models: data.models || [], source: data.source || '' }
  } catch { return null }
}

function saveCachedModels(models: string[], source: string) {
  localStorage.setItem(MODELS_CACHE_KEY, JSON.stringify({ models, source, ts: Date.now() }))
}

function loadCachedProbe(): AiModelProbeItem[] {
  try {
    const raw = localStorage.getItem(PROBE_CACHE_KEY)
    if (!raw) return []
    const data = JSON.parse(raw)
    if (Date.now() - (data.ts || 0) > CACHE_TTL_MS) return []
    return data.items || []
  } catch { return [] }
}

function saveCachedProbe(items: AiModelProbeItem[]) {
  localStorage.setItem(PROBE_CACHE_KEY, JSON.stringify({ items, ts: Date.now() }))
}

export function useSettings() {
  const notificationSettings = ref<NotificationSettings>({})
  const aiSettings = ref<AiSettings>({})
  const aiModelOptions = ref<string[]>([])
  const aiModelListSource = ref('')
  const aiModelChecks = ref<AiModelProbeItem[]>([])
  const rotationSettings = ref<RotationSettings>({})
  const systemStatus = ref<SystemStatus | null>(null)
  const isReady = ref(false)
  
  const isLoading = ref(false)
  const isSaving = ref(false)
  const isLoadingAiModels = ref(false)
  const isProbingAiModels = ref(false)
  const error = ref<Error | null>(null)

  function buildAiModelLookupPayload(overrides?: Partial<AiSettings>): AiModelListRequest {
    const source = { ...aiSettings.value, ...(overrides || {}) }
    const apiKey = (source.OPENAI_API_KEY || '').trim()
    const baseUrl = (source.OPENAI_BASE_URL || '').trim()
    const proxyUrl = (source.PROXY_URL || '').trim()
    return {
      ...(apiKey ? { OPENAI_API_KEY: apiKey } : {}),
      ...(baseUrl ? { OPENAI_BASE_URL: baseUrl } : {}),
      ...(proxyUrl ? { PROXY_URL: proxyUrl } : {})
    }
  }

  async function loadAiModels(overrides?: Partial<AiSettings>) {
    isLoadingAiModels.value = true
    error.value = null
    try {
      const response = await settingsApi.listAiModels(buildAiModelLookupPayload(overrides))
      aiModelOptions.value = response.models
      aiModelListSource.value = response.source_url
      saveCachedModels(response.models, response.source_url)

      const currentModel = (aiSettings.value.OPENAI_MODEL_NAME || '').trim()
      if (!currentModel && response.models.length > 0) {
        aiSettings.value.OPENAI_MODEL_NAME = response.models[0]
      }
      return response
    } catch (e) {
      aiModelOptions.value = []
      aiModelListSource.value = ''
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoadingAiModels.value = false
    }
  }

  async function probeAiModels(models?: string[], overrides?: Partial<AiSettings>, forceRefresh = false) {
    const targetModels = (models || aiModelOptions.value).filter(Boolean)
    if (targetModels.length === 0) {
      aiModelChecks.value = []
      return { items: [] }
    }
    isProbingAiModels.value = true
    error.value = null
    try {
      const response = await settingsApi.probeAiModels({
        ...buildAiModelLookupPayload(overrides),
        models: targetModels,
        force_refresh: forceRefresh,
      })
      aiModelChecks.value = response.items
      saveCachedProbe(response.items)
      return response
    } catch (e) {
      aiModelChecks.value = []
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isProbingAiModels.value = false
    }
  }

  async function fetchAll() {
    isLoading.value = true
    error.value = null
    try {
      const [notif, ai, rotation, status] = await Promise.all([
        settingsApi.getNotificationSettings(),
        settingsApi.getAiSettings(),
        settingsApi.getRotationSettings(),
        settingsApi.getSystemStatus()
      ])
      notificationSettings.value = notif
      aiSettings.value = ai
      rotationSettings.value = rotation
      systemStatus.value = status

      const cachedModels = loadCachedModels()
      if (cachedModels) {
        aiModelOptions.value = cachedModels.models
        aiModelListSource.value = cachedModels.source
      }
      const cachedProbe = loadCachedProbe()
      if (cachedProbe.length > 0) {
        aiModelChecks.value = cachedProbe
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      isLoading.value = false
      isReady.value = true
    }
  }

  async function refreshStatus() {
    isLoading.value = true
    error.value = null
    try {
      systemStatus.value = await settingsApi.getSystemStatus()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function saveNotificationSettings(payload: NotificationSettingsUpdate) {
    isSaving.value = true
    try {
      await settingsApi.updateNotificationSettings(payload)
      const [notif, status] = await Promise.all([
        settingsApi.getNotificationSettings(),
        settingsApi.getSystemStatus()
      ])
      notificationSettings.value = notif
      systemStatus.value = status
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSaving.value = false
    }
  }

  async function testNotification(payload: {
    channel?: string
    settings: NotificationSettingsUpdate
  }): Promise<NotificationTestResponse> {
    isSaving.value = true
    try {
      return await settingsApi.testNotificationSettings(payload)
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSaving.value = false
    }
  }

  async function saveAiSettings() {
    isSaving.value = true
    try {
      const payload = { ...aiSettings.value }
      const apiKey = (payload.OPENAI_API_KEY || '').trim()
      if (apiKey) {
        payload.OPENAI_API_KEY = apiKey
      } else {
        delete payload.OPENAI_API_KEY
      }
      await settingsApi.updateAiSettings(payload)
      if (aiSettings.value.OPENAI_API_KEY) {
        aiSettings.value.OPENAI_API_KEY = ''
      }
      systemStatus.value = await settingsApi.getSystemStatus()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSaving.value = false
    }
  }

  async function saveRotationSettings() {
    isSaving.value = true
    try {
      await settingsApi.updateRotationSettings(rotationSettings.value)
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSaving.value = false
    }
  }

  async function testAiConnection() {
    isSaving.value = true
    try {
      const payload = { ...aiSettings.value }
      const apiKey = (payload.OPENAI_API_KEY || '').trim()
      if (apiKey) {
        payload.OPENAI_API_KEY = apiKey
      } else {
        delete payload.OPENAI_API_KEY
      }
      const res = await settingsApi.testAiSettings(payload)
      return res
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSaving.value = false
    }
  }

  onMounted(fetchAll)

  return {
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
    fetchAll,
    saveNotificationSettings,
    testNotification,
    saveAiSettings,
    saveRotationSettings,
    testAiConnection,
    refreshStatus,
    loadAiModels,
    probeAiModels,
  }
}
