import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Skeleton } from './ui/Skeleton';
import {
  useForgeStateQuery,
  useForgeToolsQuery,
  useSetOverrideMutation,
} from '../api/forge';
import type { ToolStatus } from '../types/forge';
import { RiskBadge, StatusBadge } from './forge/StatusBadge';

const STATUSES: (ToolStatus | 'all')[] = [
  'all',
  'DRAFT',
  'QUARANTINED',
  'SHADOW',
  'CANARY',
  'ACTIVE',
  'DEPRECATED',
  'KILLED',
];

export function ForgePage() {
  const [statusFilter, setStatusFilter] = useState<ToolStatus | 'all'>('all');
  const stateQ = useForgeStateQuery();
  const toolsQ = useForgeToolsQuery(
    statusFilter === 'all' ? undefined : statusFilter,
  );
  const overrideMut = useSetOverrideMutation();

  const state = stateQ.data;
  const tools = toolsQ.data?.tools ?? [];

  const isEnabled = state?.effective.enabled ?? false;
  const envEnabled = state?.env.enabled ?? false;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Tool Forge</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Staged generation pipeline for agent-authored tools. Default-off,
          default-shadow, kill-sticky.
        </p>
      </div>

      {/* Master toggle banner */}
      {state && (
        <div
          className={`p-4 rounded-lg border ${
            isEnabled
              ? 'border-[#34d399]/30 bg-[#34d399]/5'
              : 'border-[#7a8599]/30 bg-[#0a0e14]'
          }`}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-3">
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${
                    isEnabled
                      ? 'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30'
                      : 'bg-[#7a8599]/15 text-[#7a8599] border-[#7a8599]/30'
                  }`}
                >
                  {isEnabled ? 'ENABLED' : 'DISABLED'}
                </span>
                {state.effective.dry_run && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-[#fbbf24]/15 text-[#fbbf24] border border-[#fbbf24]/30">
                    DRY RUN
                  </span>
                )}
                <span className="text-xs text-[#7a8599] font-mono truncate">
                  {state.effective.explanation}
                </span>
              </div>
              <p className="text-xs text-[#7a8599] mt-2">
                Env says: <span className="text-[#cbd5e1] font-mono">TOOL_FORGE_ENABLED={envEnabled ? 'true' : 'false'}</span>
                {' · '}
                Audit LLM: <span className="text-[#cbd5e1] font-mono">{state.env.audit_llm}</span>
                {' · '}
                Registry: <span className="text-[#cbd5e1] font-mono">{state.total_tools}/{state.env.max_tools}</span>
                {state.registry_full && (
                  <span className="text-[#f87171] ml-2 font-medium">FULL</span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {envEnabled && (
                <button
                  disabled={overrideMut.isPending}
                  onClick={() =>
                    overrideMut.mutate({ enabled: !state.effective.runtime_enabled })
                  }
                  className={`px-4 py-1.5 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
                    state.effective.runtime_enabled
                      ? 'bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25 border border-[#f87171]/30'
                      : 'bg-[#34d399]/15 text-[#34d399] hover:bg-[#34d399]/25 border border-[#34d399]/30'
                  }`}
                  title={
                    !envEnabled
                      ? 'Set TOOL_FORGE_ENABLED=true in env first'
                      : 'Toggle runtime override (env is the ceiling)'
                  }
                >
                  {state.effective.runtime_enabled
                    ? 'Disable forge (runtime)'
                    : 'Enable forge (runtime)'}
                </button>
              )}
              <Link
                to="/forge/compositions"
                className="px-3 py-1.5 rounded text-xs font-medium bg-[#1e2738] text-[#cbd5e1] hover:bg-[#2a3550] transition-colors"
              >
                Compositions
              </Link>
              <Link
                to="/forge/settings"
                className="px-3 py-1.5 rounded text-xs font-medium bg-[#1e2738] text-[#cbd5e1] hover:bg-[#2a3550] transition-colors"
              >
                Settings
              </Link>
            </div>
          </div>
          {!envEnabled && (
            <div className="mt-3 text-xs text-[#7a8599] border-t border-[#1e2738] pt-3">
              Env-level kill is on. The runtime override cannot enable forge
              past env. Set <code className="text-[#fbbf24]">TOOL_FORGE_ENABLED=true</code> in
              your <code>.env</code> and restart the gateway to enable.
            </div>
          )}
        </div>
      )}

      {/* Counts row */}
      {state && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
          {(
            [
              'DRAFT',
              'QUARANTINED',
              'SHADOW',
              'CANARY',
              'ACTIVE',
              'DEPRECATED',
              'KILLED',
            ] as ToolStatus[]
          ).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`p-3 rounded-lg border text-left transition-colors ${
                statusFilter === s
                  ? 'border-[#60a5fa]/40 bg-[#60a5fa]/10'
                  : 'border-[#1e2738] bg-[#111820] hover:bg-[#1e2738]'
              }`}
            >
              <div className="text-2xl font-semibold text-[#e2e8f0]">
                {state.counts[s] ?? 0}
              </div>
              <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mt-1">
                {s}
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-1.5">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-[#60a5fa]/15 text-[#60a5fa] border border-[#60a5fa]/30'
                : 'bg-[#111820] text-[#7a8599] border border-[#1e2738] hover:bg-[#1e2738]'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Tool list */}
      {toolsQ.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : tools.length === 0 ? (
        <div className="p-8 text-center text-sm text-[#7a8599] border border-[#1e2738] rounded-lg bg-[#111820]">
          No tools in this view yet.
          {statusFilter === 'all' && (
            <div className="mt-2 text-xs">
              Use <code className="text-[#fbbf24]">POST /api/forge/tools</code>{' '}
              to register one — it'll appear here once the audit pipeline runs.
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {tools.map((t) => (
            <Link
              key={t.tool_id}
              to={`/forge/${t.tool_id}`}
              className="block p-4 rounded-lg border border-[#1e2738] bg-[#111820] hover:border-[#60a5fa]/30 hover:bg-[#1e2738]/50 transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-[#e2e8f0]">
                      {t.name}
                    </span>
                    <StatusBadge status={t.status} />
                    <RiskBadge score={t.risk_score} />
                    <span className="text-[10px] font-mono text-[#7a8599]">
                      {t.source_type}
                    </span>
                    <span className="text-[10px] font-mono text-[#7a8599]">
                      v{t.version}
                    </span>
                  </div>
                  {t.description && (
                    <div className="text-xs text-[#cbd5e1] mt-1 truncate">
                      {t.description}
                    </div>
                  )}
                  {t.killed_reason && (
                    <div className="text-xs text-[#f87171] mt-1">
                      killed: {t.killed_reason}
                    </div>
                  )}
                </div>
                <div className="text-[10px] text-[#7a8599] font-mono flex-shrink-0 text-right">
                  <div>{new Date(t.status_changed_at).toLocaleString()}</div>
                  <div className="opacity-60 mt-0.5">{t.tool_id.slice(0, 16)}…</div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
