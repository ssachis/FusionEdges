import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { FiMenu, FiX } from "react-icons/fi";
import logo from "../assets/logo.png";
import "./Navbar.css";

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(window.scrollY > 10);
  const location = useLocation();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <header className={`navbar ${scrolled ? "navbar--scrolled" : ""}`}>
      <Link to="/" className="navbar__brand"><img src={logo} alt="Orbit" /> Orbit</Link>
      <button className="navbar__toggle" onClick={() => setOpen(!open)} aria-label="Menu">
        {open ? <FiX /> : <FiMenu />}
      </button>
      <nav className={`navbar__links ${open ? "is-open" : ""}`}>
        <NavLink to="/" end>Home</NavLink>
        <NavLink to="/about">About</NavLink>
        <NavLink to="/pricing">Pricing</NavLink>
      </nav>
    </header>
  );
}
