import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { UIProvider } from './context/UIContext'
import { BlockedDomainsProvider } from './context/BlockedDomainsContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <UIProvider>
        <BlockedDomainsProvider>
          <App />
        </BlockedDomainsProvider>
      </UIProvider>
    </BrowserRouter>
  </React.StrictMode>
)
