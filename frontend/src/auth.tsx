import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import type { Permission, User } from './api'

type AuthState = {
  user: User | null
  ready: boolean
  login: (login: string, password: string) => Promise<void>
  logout: () => void
  can: (permission: Permission) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    if (!getToken()) {
      setReady(true)
      return
    }
    api<User>('/auth/me')
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true))
  }, [logout])

  const login = useCallback(async (loginName: string, password: string) => {
    const result = await api<{ access_token: string; user: User }>('/auth/login', {
      method: 'POST',
      body: { login: loginName, password },
    })
    setToken(result.access_token)
    setUser(result.user)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      ready,
      login,
      logout,
      can: (permission) => !!user?.permissions.includes(permission),
    }),
    [user, ready, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth вне AuthProvider')
  return context
}
