import Footer from "./components/Footer";
import "./index.css";

export default function App() {
  return (
    <main>
      <header className="nav">
        <strong>Site A</strong>
        <nav><a href="#plans">Plans</a><a href="#about">About</a><a href="#contact">Contact</a></nav>
      </header>
      <section className="hero">
        <h1>Plan your week, calmly</h1>
        <p>A small scheduling tool for people who dislike meetings.</p>
        <input id="email" placeholder="you@example.com" />
        <button className="btn">Get early access</button>
      </section>
      <section className="cards" id="plans">
        <div className="card"><h3>Free</h3><p>One calendar, forever.</p></div>
        <div className="card"><h3>Team</h3><p>Shared focus time.</p></div>
        <div className="card"><h3>Studio</h3><p>For agencies.</p></div>
      </section>
      <Footer />
    </main>
  );
}
