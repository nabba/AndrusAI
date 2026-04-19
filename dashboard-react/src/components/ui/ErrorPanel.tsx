export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error ?? 'Unknown error');
  return (
    <div className="bg-[#111820] border border-[#f87171]/30 rounded-lg p-4 text-sm">
      <div className="text-[#f87171] font-medium mb-1">Failed to load</div>
      <div className="text-[#7a8599] text-xs mb-3 break-words">{message}</div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-xs px-3 py-1.5 border border-[#60a5fa]/30 text-[#60a5fa] rounded-lg hover:bg-[#60a5fa]/10 transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  );
}
