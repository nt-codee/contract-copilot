import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import Review from "./pages/Review";
import Library from "./pages/Library";
import Company from "./pages/Company";
import Knowledge from "./pages/Knowledge";
import { Privacy, Terms } from "./pages/Legal";
import { lazy, Suspense } from "react";
import Activity from "./pages/Activity";
import { AcceptInvite, WorkspaceSettings } from "./pages/Workspace";
import Positions from "./pages/Positions";

const Admin = lazy(() => import("./pages/Admin"));
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/reviews/:id" element={<Review />} />
        <Route path="/library" element={<Library />} />
        <Route path="/rules" element={<Company />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/knowledge/:id" element={<Knowledge />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/workspace" element={<WorkspaceSettings />} />
        <Route path="/invite/:code" element={<AcceptInvite />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/admin" element={<Suspense fallback={null}><Admin /></Suspense>} />
        <Route path="/terms" element={<Terms />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
