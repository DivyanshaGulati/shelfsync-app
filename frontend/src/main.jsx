import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './lib/AuthContext.jsx'
import InventoryPage from './pages/InventoryPage.jsx'
import VendorsPage from './pages/VendorsPage.jsx'
import PurchasingPage from './pages/PurchasingPage.jsx'
import EmployeeCheckout from './pages/EmployeeCheckout.jsx'
import SetupPage from './pages/SetupPage.jsx'
import OwnerDashboard from './pages/OwnerDashboard.jsx'
import ContactPage from './pages/ContactPage.jsx'
import './index.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider><BrowserRouter>
      <Routes>
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/inventory" element={<InventoryPage />} />
        <Route path="/vendors" element={<VendorsPage />} />
        <Route path="/purchasing" element={<PurchasingPage />} />
        <Route path="/checkout" element={<EmployeeCheckout />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="*" element={<OwnerDashboard />} />
      </Routes>
    </BrowserRouter></AuthProvider>
  </React.StrictMode>
)
