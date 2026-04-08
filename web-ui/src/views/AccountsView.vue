<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  listAccounts,
  getAccount,
  createAccount,
  updateAccount,
  deleteAccount,
  startBrowserLogin,
  getBrowserLoginJob,
  type AccountItem,
  type BrowserLoginJob,
  type BrowserLoginStatus,
} from '@/api/accounts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { toast } from '@/components/ui/toast'

const { t } = useI18n()

const accounts = ref<AccountItem[]>([])
const isLoading = ref(false)
const isSaving = ref(false)
const router = useRouter()

const isCreateDialogOpen = ref(false)
const isEditDialogOpen = ref(false)
const isDeleteDialogOpen = ref(false)
const isBrowserLoginDialogOpen = ref(false)
const isStartingBrowserLogin = ref(false)

const newName = ref('')
const newContent = ref('')
const editName = ref('')
const editContent = ref('')
const deleteName = ref('')

const browserLoginName = ref('')
const browserLoginSetAsDefault = ref(true)
const browserLoginJob = ref<BrowserLoginJob | null>(null)

let browserLoginPollTimer: number | null = null

const runningBrowserLoginStates: BrowserLoginStatus[] = ['launching', 'awaiting_scan', 'saving']

const browserLoginIsRunning = computed(() =>
  browserLoginJob.value ? runningBrowserLoginStates.includes(browserLoginJob.value.status) : false
)

const browserLoginStatusTone = computed(() => {
  const status = browserLoginJob.value?.status
  if (status === 'completed') return 'text-emerald-600'
  if (status === 'failed' || status === 'cancelled') return 'text-red-600'
  if (status === 'saving') return 'text-amber-600'
  return 'text-blue-600'
})

async function fetchAccounts() {
  isLoading.value = true
  try {
    accounts.value = await listAccounts()
  } catch (e) {
    toast({ title: t('accounts.toasts.loadFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isLoading.value = false
  }
}

function openCreateDialog() {
  newName.value = ''
  newContent.value = ''
  isCreateDialogOpen.value = true
}

function openBrowserLoginDialog() {
  browserLoginName.value = ''
  browserLoginSetAsDefault.value = true
  browserLoginJob.value = null
  isBrowserLoginDialogOpen.value = true
}

function closeBrowserLoginDialog() {
  stopBrowserLoginPolling()
  isBrowserLoginDialogOpen.value = false
  browserLoginJob.value = null
  browserLoginName.value = ''
  browserLoginSetAsDefault.value = true
  isStartingBrowserLogin.value = false
}

async function openEditDialog(name: string) {
  isSaving.value = true
  try {
    const detail = await getAccount(name)
    editName.value = detail.name
    editContent.value = detail.content
    isEditDialogOpen.value = true
  } catch (e) {
    toast({ title: t('accounts.toasts.loadContentFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isSaving.value = false
  }
}

function openDeleteDialog(name: string) {
  deleteName.value = name
  isDeleteDialogOpen.value = true
}

function goCreateTask(name: string) {
  router.push({ path: '/tasks', query: { account: name, create: '1' } })
}

function stopBrowserLoginPolling() {
  if (browserLoginPollTimer !== null) {
    window.clearTimeout(browserLoginPollTimer)
    browserLoginPollTimer = null
  }
}

function scheduleBrowserLoginPolling(jobId: string) {
  stopBrowserLoginPolling()
  browserLoginPollTimer = window.setTimeout(() => {
    void pollBrowserLogin(jobId)
  }, 1500)
}

async function pollBrowserLogin(jobId: string) {
  try {
    const previousStatus = browserLoginJob.value?.status
    const detail = await getBrowserLoginJob(jobId)
    browserLoginJob.value = detail

    if (runningBrowserLoginStates.includes(detail.status)) {
      scheduleBrowserLoginPolling(jobId)
      return
    }

    stopBrowserLoginPolling()
    if (detail.status === 'completed' && previousStatus !== 'completed') {
      toast({ title: t('accounts.toasts.browserLoginCompleted') })
      await fetchAccounts()
      closeBrowserLoginDialog()
    } else if (detail.status === 'failed' && previousStatus !== 'failed') {
      toast({ title: t('accounts.toasts.browserLoginFailed'), description: detail.error || detail.message, variant: 'destructive' })
    } else if (detail.status === 'cancelled' && previousStatus !== 'cancelled') {
      toast({ title: t('accounts.toasts.browserLoginCancelled') })
    }
  } catch (e) {
    stopBrowserLoginPolling()
    toast({ title: t('accounts.toasts.browserLoginStatusFailed'), description: (e as Error).message, variant: 'destructive' })
  }
}

function isValidJson(str: string): boolean {
  try {
    JSON.parse(str)
    return true
  } catch {
    return false
  }
}

async function handleCreateAccount() {
  if (!newName.value.trim() || !newContent.value.trim()) {
    toast({ title: t('accounts.toasts.incomplete'), description: t('accounts.toasts.createDescriptionRequired'), variant: 'destructive' })
    return
  }
  if (!isValidJson(newContent.value.trim())) {
    toast({ title: t('accounts.toasts.invalidJson'), description: t('accounts.toasts.invalidJsonDescription'), variant: 'destructive' })
    return
  }
  isSaving.value = true
  try {
    await createAccount({ name: newName.value.trim(), content: newContent.value.trim() })
    toast({ title: t('accounts.toasts.created') })
    isCreateDialogOpen.value = false
    await fetchAccounts()
  } catch (e) {
    toast({ title: t('accounts.toasts.createFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isSaving.value = false
  }
}

async function handleStartBrowserLogin() {
  if (!browserLoginName.value.trim()) {
    toast({ title: t('accounts.toasts.incomplete'), description: t('accounts.toasts.browserLoginNameRequired'), variant: 'destructive' })
    return
  }

  isStartingBrowserLogin.value = true
  try {
    const job = await startBrowserLogin({
      name: browserLoginName.value.trim(),
      set_as_default: browserLoginSetAsDefault.value,
    })
    browserLoginJob.value = job
    toast({ title: t('accounts.toasts.browserLoginStarted') })
    if (runningBrowserLoginStates.includes(job.status)) {
      scheduleBrowserLoginPolling(job.id)
    }
  } catch (e) {
    toast({ title: t('accounts.toasts.browserLoginStartFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isStartingBrowserLogin.value = false
  }
}

async function handleUpdateAccount() {
  if (!editContent.value.trim()) {
    toast({ title: t('accounts.toasts.contentRequired'), description: t('accounts.toasts.updateDescriptionRequired'), variant: 'destructive' })
    return
  }
  if (!isValidJson(editContent.value.trim())) {
    toast({ title: t('accounts.toasts.invalidJson'), description: t('accounts.toasts.invalidJsonDescription'), variant: 'destructive' })
    return
  }
  isSaving.value = true
  try {
    await updateAccount(editName.value, editContent.value.trim())
    toast({ title: t('accounts.toasts.updated') })
    isEditDialogOpen.value = false
    await fetchAccounts()
  } catch (e) {
    toast({ title: t('accounts.toasts.updateFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isSaving.value = false
  }
}

async function handleDeleteAccount() {
  isSaving.value = true
  try {
    await deleteAccount(deleteName.value)
    toast({ title: t('accounts.toasts.deleted') })
    isDeleteDialogOpen.value = false
    await fetchAccounts()
  } catch (e) {
    toast({ title: t('accounts.toasts.deleteFailed'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isSaving.value = false
  }
}

onMounted(fetchAccounts)
onScopeDispose(stopBrowserLoginPolling)
</script>

<template>
  <div>
    <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-800">{{ t('accounts.title') }}</h1>
        <p class="mt-1 text-sm text-gray-500">{{ t('accounts.description') }}</p>
      </div>
      <div class="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
        <Button class="w-full sm:w-auto" variant="outline" @click="openBrowserLoginDialog">
          {{ t('accounts.browserLogin.trigger') }}
        </Button>
        <Button class="w-full sm:w-auto" @click="openCreateDialog">{{ t('accounts.add') }}</Button>
      </div>
    </div>

    <Card class="app-surface mb-6 border-none">
      <CardHeader>
        <CardTitle>{{ t('accounts.cookieGuide.title') }}</CardTitle>
        <CardDescription>{{ t('accounts.cookieGuide.description') }}</CardDescription>
      </CardHeader>
      <CardContent class="space-y-4 text-sm text-gray-600">
        <div class="rounded-lg border border-emerald-100 bg-emerald-50/80 p-4 text-emerald-700">
          <p class="font-medium">{{ t('accounts.cookieGuide.recommendedTitle') }}</p>
          <p class="mt-1">{{ t('accounts.cookieGuide.recommendedDescription') }}</p>
        </div>
        <ol class="list-decimal list-inside space-y-1">
          <li>{{ t('accounts.cookieGuide.stepBrowser1') }}</li>
          <li>{{ t('accounts.cookieGuide.stepBrowser2') }}</li>
          <li>{{ t('accounts.cookieGuide.stepBrowser3') }}</li>
        </ol>
        <div class="border-t border-slate-200 pt-4">
          <p class="font-medium text-slate-700">{{ t('accounts.cookieGuide.fallbackTitle') }}</p>
          <ol class="mt-2 list-decimal list-inside space-y-1">
            <li>
              {{ t('accounts.cookieGuide.step1Prefix') }}
              <a
                class="text-blue-600 hover:underline"
                href="https://chromewebstore.google.com/detail/xianyu-login-state-extrac/eidlpfjiodpigmfcahkmlenhppfklcoa"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('accounts.cookieGuide.extension') }}</a>
            </li>
            <li>
              {{ t('accounts.cookieGuide.step2Prefix') }}
              <a
                class="text-blue-600 hover:underline"
                href="https://www.goofish.com"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t('accounts.cookieGuide.website') }}</a>
            </li>
            <li>{{ t('accounts.cookieGuide.step3') }}</li>
            <li>{{ t('accounts.cookieGuide.step4') }}</li>
            <li>{{ t('accounts.cookieGuide.step5') }}</li>
          </ol>
        </div>
      </CardContent>
    </Card>

    <Card class="app-surface border-none">
      <CardHeader>
        <CardTitle>{{ t('accounts.list.title') }}</CardTitle>
        <CardDescription>{{ t('accounts.list.description') }}</CardDescription>
      </CardHeader>
      <CardContent>
        <div class="space-y-4 md:hidden">
          <div v-if="isLoading" class="py-10 text-center text-sm text-muted-foreground">{{ t('common.loading') }}</div>
          <div v-else-if="accounts.length === 0" class="py-10 text-center text-sm text-muted-foreground">{{ t('accounts.list.empty') }}</div>
          <article
            v-else
            v-for="account in accounts"
            :key="account.name"
            class="app-surface-subtle p-4"
          >
            <div class="space-y-2">
              <div class="flex items-center justify-between gap-3">
                <h3 class="truncate text-base font-semibold text-slate-900">{{ account.name }}</h3>
                <Button size="sm" variant="outline" @click="goCreateTask(account.name)">{{ t('accounts.list.createTask') }}</Button>
              </div>
              <p class="break-all text-sm text-slate-500">{{ account.path }}</p>
            </div>
            <div class="mt-4 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" class="min-w-[120px] flex-1" @click="openEditDialog(account.name)">{{ t('accounts.list.update') }}</Button>
              <Button size="sm" variant="destructive" class="min-w-[120px] flex-1" @click="openDeleteDialog(account.name)">{{ t('accounts.list.delete') }}</Button>
            </div>
          </article>
        </div>

        <div class="hidden md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{{ t('accounts.list.name') }}</TableHead>
                <TableHead>{{ t('accounts.list.file') }}</TableHead>
                <TableHead class="text-right">{{ t('accounts.list.actions') }}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-if="isLoading">
                <TableCell colspan="3" class="h-20 text-center text-muted-foreground">{{ t('common.loading') }}</TableCell>
              </TableRow>
              <TableRow v-else-if="accounts.length === 0">
                <TableCell colspan="3" class="h-20 text-center text-muted-foreground">{{ t('accounts.list.empty') }}</TableCell>
              </TableRow>
              <TableRow v-else v-for="account in accounts" :key="account.name">
                <TableCell class="font-medium">{{ account.name }}</TableCell>
                <TableCell class="text-sm text-gray-500">{{ account.path }}</TableCell>
                <TableCell class="text-right">
                  <div class="flex justify-end gap-2">
                    <Button size="sm" variant="outline" @click="goCreateTask(account.name)">{{ t('accounts.list.createTask') }}</Button>
                    <Button size="sm" variant="outline" @click="openEditDialog(account.name)">{{ t('accounts.list.update') }}</Button>
                    <Button size="sm" variant="destructive" @click="openDeleteDialog(account.name)">{{ t('accounts.list.delete') }}</Button>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>

    <Dialog v-model:open="isCreateDialogOpen">
      <DialogContent class="sm:max-w-[700px]">
        <DialogHeader>
          <DialogTitle>{{ t('accounts.createDialog.title') }}</DialogTitle>
          <DialogDescription>{{ t('accounts.createDialog.description') }}</DialogDescription>
        </DialogHeader>
        <div class="space-y-4">
          <div class="grid gap-2">
            <Label>{{ t('accounts.createDialog.name') }}</Label>
            <Input v-model="newName" :placeholder="t('accounts.createDialog.namePlaceholder')" />
          </div>
          <div class="grid gap-2">
            <Label>{{ t('accounts.createDialog.jsonContent') }}</Label>
            <Textarea v-model="newContent" class="min-h-[200px]" :placeholder="t('accounts.createDialog.jsonPlaceholder')" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="isCreateDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button :disabled="isSaving" @click="handleCreateAccount">
            {{ isSaving ? t('common.saving') : t('common.save') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isBrowserLoginDialogOpen">
      <DialogContent class="sm:max-w-[620px]">
        <DialogHeader>
          <DialogTitle>{{ t('accounts.browserLogin.title') }}</DialogTitle>
          <DialogDescription>{{ t('accounts.browserLogin.description') }}</DialogDescription>
        </DialogHeader>
        <div class="space-y-5">
          <div class="grid gap-2">
            <Label>{{ t('accounts.browserLogin.accountName') }}</Label>
            <Input v-model="browserLoginName" :placeholder="t('accounts.browserLogin.accountNamePlaceholder')" :disabled="browserLoginIsRunning" />
          </div>
          <div class="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3">
            <div>
              <p class="text-sm font-medium text-slate-900">{{ t('accounts.browserLogin.setDefaultTitle') }}</p>
              <p class="text-xs text-slate-500">{{ t('accounts.browserLogin.setDefaultDescription') }}</p>
            </div>
            <Switch v-model:checked="browserLoginSetAsDefault" :disabled="browserLoginIsRunning" />
          </div>
          <div class="rounded-lg border border-slate-200 bg-slate-50/70 p-4">
            <p class="text-sm font-medium text-slate-900">{{ t('accounts.browserLogin.statusTitle') }}</p>
            <p class="mt-2 text-sm" :class="browserLoginStatusTone">
              {{ browserLoginJob?.message || t('accounts.browserLogin.idleStatus') }}
            </p>
            <div v-if="browserLoginJob" class="mt-3 space-y-1 text-xs text-slate-500">
              <p>{{ t('accounts.browserLogin.currentAccount', { name: browserLoginJob.account_name }) }}</p>
              <p>{{ t('accounts.browserLogin.currentState', { status: t(`accounts.browserLogin.statuses.${browserLoginJob.status}`) }) }}</p>
              <p v-if="browserLoginJob.default_state_path">{{ t('accounts.browserLogin.defaultStateHint') }}</p>
            </div>
          </div>
          <ol class="list-decimal list-inside space-y-1 text-sm text-slate-600">
            <li>{{ t('accounts.browserLogin.step1') }}</li>
            <li>{{ t('accounts.browserLogin.step2') }}</li>
            <li>{{ t('accounts.browserLogin.step3') }}</li>
          </ol>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="closeBrowserLoginDialog">{{ t('common.cancel') }}</Button>
          <Button :disabled="isStartingBrowserLogin || browserLoginIsRunning" @click="handleStartBrowserLogin">
            {{ isStartingBrowserLogin ? t('accounts.browserLogin.starting') : t('accounts.browserLogin.start') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isEditDialogOpen">
      <DialogContent class="sm:max-w-[700px]">
        <DialogHeader>
          <DialogTitle>{{ t('accounts.editDialog.title', { name: editName }) }}</DialogTitle>
          <DialogDescription>{{ t('accounts.editDialog.description') }}</DialogDescription>
        </DialogHeader>
        <div class="space-y-4">
          <div class="grid gap-2">
            <Label>{{ t('accounts.createDialog.jsonContent') }}</Label>
            <Textarea v-model="editContent" class="min-h-[200px]" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="isEditDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button :disabled="isSaving" @click="handleUpdateAccount">
            {{ isSaving ? t('common.saving') : t('common.save') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isDeleteDialogOpen">
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{{ t('accounts.deleteDialog.title') }}</DialogTitle>
          <DialogDescription>{{ t('accounts.deleteDialog.description', { name: deleteName }) }}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isDeleteDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" :disabled="isSaving" @click="handleDeleteAccount">
            {{ isSaving ? t('accounts.deleteDialog.deleting') : t('accounts.list.delete') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
