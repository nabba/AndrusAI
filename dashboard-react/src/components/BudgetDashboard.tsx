import { useState } from 'react';
import { useProject } from '../context/useProject';
import type { Budget } from '../types';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import { useBudgetsQuery, useOverrideBudget } from '../api/queries';
import { CreditAlertsPanel } from './CreditAlertsPanel';

function OverrideModal({
  budget,
  onClose,
}: {
  budget: Budget;
  onClose: () => void;
}) {
  const [newLimit, setNewLimit] = useState(budget.limit_usd.toString());
  const [reason, setReason] = useState('');
  const [localError, setLocalError] = useState('');
  const override = useOverrideBudget();

  const handleSubmit = async () => {
    const limit = parseFloat(newLimit);
    if (isNaN(limit) || limit <= 0) {
      setLocalError('Please enter a valid limit greater than 0.');
      return;
    }
    setLocalError('');
    try {
      await override.mutateAsync({
        budget_id: budget.agent_role,
        new_limit: limit,
        reason: reason || undefined,
      });
      onClose();
    } catch {
      // error surfaced via override.error
    }
  };

  const error = localError || (override.error instanceof Error ? override.error.message : '');

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[#111820] border border-[#1e2738] rounded-xl w-full max-w-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-[#1e2738]">
          <h2 className="text-base font-semibold text-[#e2e8f0]">Override Budget</h2>
          <button onClick={onClose} aria-label="Close" className="text-[#7a8599] hover:text-[#e2e8f0]">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-4 space-y-4">
          <div>
            <p className="text-sm text-[#7a8599] mb-1">Agent</p>
            <p className="text-sm text-[#e2e8f0] font-medium">{budget.agent_role}</p>
          </div>
          <div>
            <label className="text-sm text-[#7a8599] block mb-1">New Limit ($)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={newLimit}
              onChange={(e) => setNewLimit(e.target.value)}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded-lg px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            />
          </div>
          <div>
            <label className="text-sm text-[#7a8599] block mb-1">Reason (optional)</label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded-lg px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa] resize-none"
              placeholder="Why are you overriding this limit?"
            />
          </div>
          {error && <p className="text-sm text-[#f87171]">{error}</p>}
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 border border-[#1e2738] text-[#7a8599] text-sm rounded-lg hover:bg-[#1e2738] transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={override.isPending}
              className="flex-1 px-4 py-2 bg-[#60a5fa]/20 border border-[#60a5fa]/30 text-[#60a5fa] text-sm rounded-lg hover:bg-[#60a5fa]/30 disabled:opacity-50 transition-colors"
            >
              {override.isPending ? 'Saving...' : 'Apply Override'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function BudgetDashboard() {
  const { activeProject } = useProject();
  const [overrideBudget, setOverrideBudget] = useState<Budget | null>(null);
  const { data: budgets, isLoading, error, refetch } = useBudgetsQuery(activeProject?.id);

  const totalSpent = budgets?.reduce((s, b) => s + b.spent_usd, 0) ?? 0;
  const totalLimit = budgets?.reduce((s, b) => s + b.limit_usd, 0) ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Budgets</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          {activeProject ? activeProject.name : 'All projects'}
        </p>
      </div>

      {/* Credit-exhaustion alerts (renders nothing when no providers
          are flagged). Sits at the top so depleted providers are
          impossible to miss. */}
      <CreditAlertsPanel />

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-[#7a8599]">Monthly Total</h2>
          <span className="text-sm text-[#e2e8f0]">
            ${totalSpent.toFixed(4)} / ${totalLimit.toFixed(4)}
          </span>
        </div>
        <div className="w-full bg-[#1e2738] rounded-full h-3">
          <div
            className={`h-3 rounded-full transition-all ${
              totalLimit > 0 && totalSpent / totalLimit > 0.85
                ? 'bg-[#f87171]'
                : totalLimit > 0 && totalSpent / totalLimit > 0.6
                ? 'bg-[#fbbf24]'
                : 'bg-[#34d399]'
            }`}
            style={{
              width: `${totalLimit > 0 ? Math.min((totalSpent / totalLimit) * 100, 100) : 0}%`,
            }}
          />
        </div>
        <div className="flex justify-between text-xs text-[#7a8599] mt-1">
          <span>$0</span>
          <span>
            {totalLimit > 0
              ? `${Math.round((totalSpent / totalLimit) * 100)}% used`
              : 'No limit set'}
          </span>
          <span>${totalLimit.toFixed(2)}</span>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : error ? (
        <ErrorPanel error={error} onRetry={refetch} />
      ) : !budgets || budgets.length === 0 ? (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-8 text-center">
          <p className="text-[#7a8599]">No budgets configured.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {budgets.map((budget) => {
            const pct = budget.limit_usd > 0 ? Math.min((budget.spent_usd / budget.limit_usd) * 100, 100) : 0;
            const barColor =
              pct > 85 ? 'bg-[#f87171]' : pct > 60 ? 'bg-[#fbbf24]' : 'bg-[#34d399]';
            const rowKey = `${budget.project_name ?? 'default'}:${budget.agent_role}:${budget.period}`;

            return (
              <div
                key={rowKey}
                className="bg-[#111820] border border-[#1e2738] rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {budget.is_paused && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-[#f87171]/20 text-[#f87171] border border-[#f87171]/30">
                        PAUSED
                      </span>
                    )}
                    <span className="text-sm font-medium text-[#e2e8f0]">{budget.agent_role}</span>
                    {budget.project_name && (
                      <span className="text-[10px] text-[#7a8599] uppercase tracking-wider">
                        · {budget.project_name}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => setOverrideBudget(budget)}
                    className="text-xs px-2.5 py-1 border border-[#1e2738] text-[#7a8599] rounded-lg hover:border-[#60a5fa] hover:text-[#60a5fa] transition-colors"
                  >
                    Override
                  </button>
                </div>

                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs text-[#7a8599]">
                    <span>${budget.spent_usd.toFixed(4)} spent</span>
                    <span>${budget.limit_usd.toFixed(4)} limit</span>
                  </div>
                  <div className="w-full bg-[#1e2738] rounded-full h-2">
                    <div
                      className={`h-2 rounded-full ${barColor} transition-all`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="flex justify-between text-xs">
                    <span
                      className={
                        pct > 85
                          ? 'text-[#f87171]'
                          : pct > 60
                          ? 'text-[#fbbf24]'
                          : 'text-[#34d399]'
                      }
                    >
                      {pct.toFixed(1)}% used
                    </span>
                    <span className="text-[#7a8599]">
                      ${(budget.limit_usd - budget.spent_usd).toFixed(4)} remaining
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {overrideBudget && (
        <OverrideModal
          budget={overrideBudget}
          onClose={() => setOverrideBudget(null)}
        />
      )}
    </div>
  );
}
