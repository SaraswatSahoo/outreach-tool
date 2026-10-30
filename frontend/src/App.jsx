import { useState, useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import api from './api'
import Navbar from './components/Navbar'
import ErrorBoundary from './components/ErrorBoundary'
import Dashboard from './pages/Dashboard'
import Profile from './pages/Profile'
import Settings from './pages/Settings'
import NewCampaign from './pages/NewCampaign'
import Preview from './pages/Preview'
import Search from './pages/Search'
import SendProgress from './pages/SendProgress'
import Campaigns from './pages/Campaigns'
import CampaignDetail from './pages/CampaignDetail'
import Pipeline from './pages/Pipeline'
import NotFound from './pages/NotFound'

export default function App() {
  const [profileComplete, setProfileComplete] = useState(null)
  const location = useLocation()

  // Only needs checking until the profile is complete
  useEffect(() => {
    if (profileComplete) return
    api.get('/profile')
      .then((res) => setProfileComplete(res.data.is_complete))
      .catch(() => setProfileComplete(false))
  }, [location.pathname, profileComplete])

  if (profileComplete === null) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    )
  }

  // Everything except the profile page requires a completed profile
  const gated = (element) => (profileComplete ? element : <Navigate to="/profile" replace />)

  return (
    <div className="min-h-screen bg-zinc-50 font-sans text-zinc-950">
      <Navbar />
      <main className="max-w-6xl mx-auto px-4 py-8">
        <ErrorBoundary key={location.pathname}>
          <Routes>
            <Route path="/" element={gated(<Dashboard />)} />
            <Route path="/profile" element={<Profile onSave={(complete) => setProfileComplete(complete)} />} />
            <Route path="/settings" element={gated(<Settings />)} />
            <Route path="/campaign/new" element={gated(<NewCampaign />)} />
            <Route path="/campaigns" element={gated(<Campaigns />)} />
            <Route path="/search" element={gated(<Search />)} />
            <Route path="/pipeline" element={gated(<Pipeline />)} />
            <Route path="/campaign/:campaignId" element={gated(<CampaignDetail />)} />
            <Route path="/campaign/:campaignId/preview" element={gated(<Preview />)} />
            <Route path="/campaign/:campaignId/send" element={gated(<SendProgress />)} />
            <Route path="/campaign/:campaignId/followup/preview" element={gated(<Preview isFollowUp />)} />
            <Route path="/campaign/:campaignId/followup/send" element={gated(<SendProgress isFollowUp />)} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  )
}
