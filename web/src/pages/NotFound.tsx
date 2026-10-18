import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-5 py-16 text-white">
      <h1 className="display text-2xl">Page not found</h1>
      <p className="mt-3 text-desk-text">The link may be old or mistyped.</p>
      <Link to="/" className="btn-go mt-6">
        Review a contract
      </Link>
    </div>
  );
}
