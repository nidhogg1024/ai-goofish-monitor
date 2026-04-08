import { ref, computed, getCurrentInstance } from 'vue'
import { useRouter, type Router } from 'vue-router'
import { wsService } from '@/services/websocket'
import { setHttpUnauthorizedHandler } from '@/lib/http'

const username = ref<string | null>(localStorage.getItem('auth_username'))
const isLoggedIn = ref(localStorage.getItem('auth_logged_in') === 'true')

let _router: Router | null = null

export function bindAuthRouter(router: Router) {
  _router = router
}

function getRouter(): Router | null {
  return _router
}

export function useAuth() {
  if (getCurrentInstance()) {
    try {
      _router = useRouter()
    } catch {
      // guard against edge cases
    }
  }

  const isAuthenticated = computed(() => isLoggedIn.value)

  function setAuthenticated(user: string) {
    username.value = user
    isLoggedIn.value = true

    localStorage.setItem('auth_username', user)
    localStorage.setItem('auth_logged_in', 'true')

    wsService.start()
  }

  function logout() {
    username.value = null
    isLoggedIn.value = false
    localStorage.removeItem('auth_username')
    localStorage.removeItem('auth_logged_in')

    wsService.stop()

    const router = getRouter()
    if (router) {
      router.push('/login')
    } else {
      window.location.href = '/login'
    }
  }

  // TODO: Passwords are transmitted in plaintext; ensure HTTPS in production.
  async function login(user: string, pass: string): Promise<boolean> {
    try {
      const response = await fetch('/auth/status', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username: user, password: pass }),
      })

      if (response.ok) {
        setAuthenticated(user)
        return true
      } else {
        return false
      }
    } catch (e) {
      console.error('Login error', e)
      return false
    }
  }

  async function validateSession(): Promise<boolean> {
    if (!isLoggedIn.value) return false
    try {
      const response = await fetch('/auth/status', { method: 'GET' })
      if (!response.ok) {
        logout()
        return false
      }
      return true
    } catch {
      return false
    }
  }

  return {
    username,
    isAuthenticated,
    login,
    logout,
    validateSession,
  }
}

setHttpUnauthorizedHandler(() => {
  const { logout } = useAuth()
  logout()
})
