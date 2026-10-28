import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { useAuthStore } from "@/stores/authStore";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { Calls } from "@/pages/Calls";
import { CallDetails } from "@/pages/CallDetails";
import { Leads } from "@/pages/Leads";
import { LeadDetails } from "@/pages/LeadDetails";
import { Departments } from "@/pages/Departments";
import { Transcripts } from "@/pages/Transcripts";
import { Emails } from "@/pages/Emails";
import { Recordings } from "@/pages/Recordings";
import { Usage } from "@/pages/Usage";
import { Analytics } from "@/pages/Analytics";
import { Settings } from "@/pages/Settings";

function ProtectedShell() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/calls" element={<Calls />} />
        <Route path="/calls/:id" element={<CallDetails />} />
        <Route path="/leads" element={<Leads />} />
        <Route path="/leads/:id" element={<LeadDetails />} />
        <Route path="/departments" element={<Departments />} />
        <Route path="/transcripts" element={<Transcripts />} />
        <Route path="/emails" element={<Emails />} />
        <Route path="/recordings" element={<Recordings />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

export function AppRouter() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <Login />}
      />
      <Route path="/*" element={<ProtectedShell />} />
    </Routes>
  );
}
