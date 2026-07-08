import { Routes, Route } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { LivePage } from './pages/LivePage'
import { IncidentsPage } from './pages/IncidentsPage'
import { IncidentDetailPage } from './pages/IncidentDetailPage'
import { PendingPage } from './pages/PendingPage'
import { PendingDetailPage } from './pages/PendingDetailPage'
import { KnowledgePage } from './pages/KnowledgePage'
import { KnowledgeCreatePage } from './pages/KnowledgeCreatePage'
import { KnowledgeDetailPage } from './pages/KnowledgeDetailPage'

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<LivePage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/incidents/:id" element={<IncidentDetailPage />} />
        <Route path="/pending" element={<PendingPage />} />
        <Route path="/pending/:id" element={<PendingDetailPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/knowledge/new" element={<KnowledgeCreatePage />} />
        <Route path="/knowledge/:key" element={<KnowledgeDetailPage />} />
      </Routes>
    </AppShell>
  )
}
