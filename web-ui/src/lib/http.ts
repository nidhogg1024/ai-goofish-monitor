type LogoutCallback = () => void
let _onUnauthorized: LogoutCallback | null = null

export function setHttpUnauthorizedHandler(cb: LogoutCallback) {
  _onUnauthorized = cb
}

const DEFAULT_TIMEOUT_MS = 30_000

interface FetchOptions extends RequestInit {
  params?: Record<string, string | number | boolean | undefined>;
  timeoutMs?: number;
}

export async function http(url: string, options: FetchOptions = {}) {
  const headers = new Headers(options.headers)

  const csrfToken = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content
  if (csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }

  let fullUrl = url
  if (options.params) {
    const searchParams = new URLSearchParams()
    Object.entries(options.params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        searchParams.append(key, String(value))
      }
    })
    const queryString = searchParams.toString()
    if (queryString) {
      fullUrl += (url.includes('?') ? '&' : '?') + queryString
    }
  }

  const controller = new AbortController()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  const config: RequestInit = {
    ...options,
    headers,
    signal: controller.signal,
  }

  let response: Response
  try {
    response = await fetch(fullUrl, config)
  } catch (e) {
    if ((e as DOMException)?.name === 'AbortError') {
      throw new Error(`Request timeout after ${timeoutMs}ms`)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }

  if (response.status === 401) {
    _onUnauthorized?.()
    throw new Error('Unauthorized')
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
  }

  if (response.status === 204) {
    return null
  }

  return response.json()
}
