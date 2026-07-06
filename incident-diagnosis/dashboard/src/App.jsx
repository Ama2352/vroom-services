import { Routes, Route } from 'react-router-dom'
import NavBar from './components/NavBar.jsx'
import LivePage from './pages/LivePage.jsx'
import IncidentsPage from './pages/IncidentsPage.jsx'
import IncidentDetailPage from './pages/IncidentDetailPage.jsx'
import PendingPage from './pages/PendingPage.jsx'
import PendingDetailPage from './pages/PendingDetailPage.jsx'
import KnowledgePage from './pages/KnowledgePage.jsx'
import KnowledgeDetailPage from './pages/KnowledgeDetailPage.jsx'

export default function App() {
  return (
    <div className="app">
      <NavBar />
      <main className="content">
        <Routes>
          <Route path="/" element={<LivePage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage />} />
          <Route path="/pending" element={<PendingPage />} />
          <Route path="/pending/:id" element={<PendingDetailPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/knowledge/:key" element={<KnowledgeDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}
