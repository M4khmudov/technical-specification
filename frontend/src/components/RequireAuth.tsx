import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { Loading } from './ui'

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth()
  const location = useLocation()
  if (!ready) return <Loading />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return children
}
