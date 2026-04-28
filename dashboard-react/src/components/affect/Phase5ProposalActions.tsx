import { useState } from 'react';
import { useReviewProposal } from '../../api/affect';

interface Phase5ProposalActionsProps {
  featureName: string;
  onReviewed?: () => void;
}

const ACTIONS: Array<{ value: 'approve' | 'defer' | 'reject'; label: string; color: string; bg: string }> = [
  { value: 'approve', label: 'Approve', color: '#34d399', bg: '#34d3991a' },
  { value: 'defer',   label: 'Defer',   color: '#fbbf24', bg: '#fbbf241a' },
  { value: 'reject',  label: 'Reject',  color: '#f87171', bg: '#f871711a' },
];

export function Phase5ProposalActions({ featureName, onReviewed }: Phase5ProposalActionsProps) {
  const [note, setNote] = useState('');
  const review = useReviewProposal();

  const handle = (action: 'approve' | 'defer' | 'reject') => {
    review.mutate(
      { featureName, action, note: note.trim() || undefined },
      {
        onSuccess: () => {
          setNote('');
          onReviewed?.();
        },
      },
    );
  };

  return (
    <div className="mt-2 pt-2 border-t border-[#1e2738]">
      <div className="flex gap-1.5 mb-1.5 flex-wrap">
        {ACTIONS.map((a) => (
          <button
            key={a.value}
            type="button"
            disabled={review.isPending || !!review.data?.found}
            onClick={() => handle(a.value)}
            className="text-[10px] px-2 py-0.5 rounded font-mono border border-transparent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ color: a.color, background: a.bg }}
          >
            {a.label}
          </button>
        ))}
        {review.data?.found ? (
          <span className="text-[10px] px-2 py-0.5 rounded font-mono text-[#34d399] bg-[#34d3991a]">
            reviewed → {review.data.proposal?.review_status}
          </span>
        ) : null}
        {review.isError ? (
          <span className="text-[10px] px-2 py-0.5 rounded font-mono text-[#f87171] bg-[#f871711a]" title={(review.error as Error)?.message}>
            error
          </span>
        ) : null}
      </div>
      <input
        type="text"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="optional review note"
        disabled={review.isPending || !!review.data?.found}
        className="w-full px-2 py-0.5 rounded bg-[#0a0e14] border border-[#1e2738] text-[11px] text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa] disabled:opacity-50"
      />
    </div>
  );
}
