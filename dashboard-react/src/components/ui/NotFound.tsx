import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div className="text-center space-y-3">
        <div className="text-5xl">🛰️</div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Page not found</h1>
        <p className="text-sm text-[#7a8599]">That route doesn't match anything on this dashboard.</p>
        <Link
          to="/"
          className="inline-block px-4 py-2 bg-[#60a5fa]/20 border border-[#60a5fa]/30 text-[#60a5fa] text-sm rounded-lg hover:bg-[#60a5fa]/30 transition-colors"
        >
          Back to dashboard
        </Link>
      </div>
    </div>
  );
}
