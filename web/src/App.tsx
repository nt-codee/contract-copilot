import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import Review from "./pages/Review";
import Library from "./pages/Library";
import Company from "./pages/Company";
import Knowledge from "./pages/Knowledge";
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
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
