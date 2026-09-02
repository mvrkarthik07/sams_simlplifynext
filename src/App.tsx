import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './pages/Dashboard';
import { FindingDetail } from './pages/FindingDetail';
import { AuditLog } from './pages/AuditLog';
import { PolicyTiers } from './pages/PolicyTiers';

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen w-full bg-background text-foreground overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/finding/:id" element={<FindingDetail />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="/policy" element={<PolicyTiers />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
