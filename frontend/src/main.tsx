import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth'
import Layout from './components/Layout'
import RequireAuth from './components/RequireAuth'
import './index.css'
import AssistantPage from './pages/AssistantPage'
import AuditPage from './pages/AuditPage'
import BatchesPage from './pages/BatchesPage'
import BatchPage from './pages/BatchPage'
import JobPage from './pages/JobPage'
import JobsPage from './pages/JobsPage'
import LoginPage from './pages/LoginPage'
import ReceiptPage from './pages/ReceiptPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/batches" replace />} />
            <Route path="/batches" element={<BatchesPage />} />
            <Route path="/batches/:id" element={<BatchPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/jobs/:id" element={<JobPage />} />
            <Route path="/receipts/new" element={<ReceiptPage />} />
            <Route path="/audit" element={<AuditPage />} />
            {/* Один маршрут: при создании диалога адрес меняется, а страница (и поток ответа) — нет */}
            <Route path="/assistant/:id?" element={<AssistantPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
)
