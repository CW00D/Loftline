import { Link } from "react-router-dom";

function NotFound() {
  return (
    <main className="hero">
      <div className="hero__inner">
        <h1 className="hero__title">Not found</h1>
        <p className="hero__subtitle">There is nothing at this address.</p>
        <nav className="hero__actions">
          <Link to="/" className="button">Home</Link>
        </nav>
      </div>
    </main>
  );
}

export default NotFound;
