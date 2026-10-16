import { BrowserRouter, Routes, Route } from "react-router-dom";
import Navbar from "./components/Navbar";

export default function App() {
  return (
    <BrowserRouter>
      <Navbar />
      <Routes>
        <Route path="/" element={<h1>Orbit</h1>} />
        <Route path="/about" element={<h1>About</h1>} />
        <Route path="/pricing" element={<h1>Pricing</h1>} />
      </Routes>
    </BrowserRouter>
  );
}
