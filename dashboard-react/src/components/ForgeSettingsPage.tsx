import { Link } from 'react-router-dom';
import {
  useForgeStateQuery,
  useSetOverrideMutation,
} from '../api/forge';
import { Skeleton } from './ui/Skeleton';

export function ForgeSettingsPage() {
  const stateQ = useForgeStateQuery();
  const overrideMut = useSetOverrideMutation();
  const state = stateQ.data;

  if (!state) return <Skeleton className="h-96" />;

  const envOff = !state.env.enabled;

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <Link
          to="/forge"
          className="text-xs text-[#7a8599] hover:text-[#60a5fa]"
        >
          ← Back to Forge
        </Link>
        <h1 className="text-xl font-semibold text-[#e2e8f0] mt-2">
          Forge Settings
        </h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Three-layer kill resolution: env (ceiling) · runtime override (this
          page) · per-tool kill (sticky). UI cannot enable forge past the env
          ceiling.
        </p>
      </div>

      {/* Resolved state banner */}
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820]">
        <h3 className="text-sm font-semibold text-[#e2e8f0] mb-3">
          Effective state
        </h3>
        <div className="font-mono text-xs text-[#cbd5e1] bg-[#0a0e14] p-3 rounded border border-[#1e2738]">
          {state.effective.explanation}
        </div>
        <div className="grid grid-cols-3 gap-3 mt-3">
          <Pill label="env" on={state.effective.env_enabled} />
          <Pill label="runtime" on={state.effective.runtime_enabled} />
          <Pill label="effective" on={state.effective.enabled} prominent />
        </div>
      </div>

      {/* Master toggles */}
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820] space-y-4">
        <div>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-[#e2e8f0]">
                Runtime override
              </div>
              <div className="text-xs text-[#7a8599] mt-1">
                Set via UI, persisted in DB. Cannot enable forge if env is off.
              </div>
            </div>
            <label className="inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={state.effective.runtime_enabled}
                disabled={envOff || overrideMut.isPending}
                onChange={(e) =>
                  overrideMut.mutate({ enabled: e.target.checked })
                }
                className="sr-only peer"
              />
              <div className="w-12 h-6 bg-[#1e2738] peer-checked:bg-[#34d399] rounded-full peer peer-checked:after:translate-x-6 after:content-[''] after:absolute after:top-0.5 after:start-0.5 after:bg-[#0a0e14] after:rounded-full after:h-5 after:w-5 after:transition-all relative peer-disabled:opacity-50 peer-disabled:cursor-not-allowed" />
            </label>
          </div>
        </div>

        <div className="border-t border-[#1e2738] pt-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-[#e2e8f0]">
                Dry run
              </div>
              <div className="text-xs text-[#7a8599] mt-1">
                Tools register and audit normally, but invocations are no-ops.
              </div>
            </div>
            <label className="inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={state.effective.dry_run}
                disabled={overrideMut.isPending}
                onChange={(e) =>
                  overrideMut.mutate({ dry_run: e.target.checked })
                }
                className="sr-only peer"
              />
              <div className="w-12 h-6 bg-[#1e2738] peer-checked:bg-[#fbbf24] rounded-full peer peer-checked:after:translate-x-6 after:content-[''] after:absolute after:top-0.5 after:start-0.5 after:bg-[#0a0e14] after:rounded-full after:h-5 after:w-5 after:transition-all relative" />
            </label>
          </div>
        </div>

        {envOff && (
          <div className="text-xs text-[#fbbf24] border-t border-[#1e2738] pt-4">
            Env-level kill is on (TOOL_FORGE_ENABLED=false). Set it to{' '}
            <code className="text-[#34d399]">true</code> in <code>.env</code> and
            restart the gateway before the runtime toggle takes effect.
          </div>
        )}
      </div>

      {/* Read-only env mirror */}
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820]">
        <h3 className="text-sm font-semibold text-[#e2e8f0] mb-3">
          Env values (read-only)
        </h3>
        <p className="text-xs text-[#7a8599] mb-3">
          These come from <code>.env</code>. Editing them requires changing the
          file and restarting the gateway — UI is intentionally not authoritative.
        </p>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-xs">
          <EnvRow k="TOOL_FORGE_ENABLED" v={String(state.env.enabled)} />
          <EnvRow
            k="TOOL_FORGE_REQUIRE_HUMAN_PROMOTION"
            v={String(state.env.require_human_promotion)}
          />
          <EnvRow k="TOOL_FORGE_MAX_TOOLS" v={String(state.env.max_tools)} />
          <EnvRow
            k="TOOL_FORGE_MAX_CALLS_PER_TOOL_PER_HOUR"
            v={String(state.env.max_calls_per_tool_per_hour)}
          />
          <EnvRow
            k="TOOL_FORGE_MAX_TOOLS_PER_PLAN"
            v={String(state.env.max_tools_per_plan)}
          />
          <EnvRow k="TOOL_FORGE_AUDIT_LLM" v={state.env.audit_llm} />
          <EnvRow
            k="TOOL_FORGE_SHADOW_RUNS_REQUIRED"
            v={String(state.env.shadow_runs_required)}
          />
          <EnvRow k="TOOL_FORGE_DRY_RUN" v={String(state.env.dry_run)} />
          <EnvRow
            k="TOOL_FORGE_COMPOSITION_RISK_THRESHOLD"
            v={String(state.env.composition_risk_threshold)}
          />
          <EnvRow
            k="TOOL_FORGE_BLOCKED_DOMAINS"
            v={state.env.blocked_domains.join(', ') || '(none)'}
          />
          <EnvRow
            k="TOOL_FORGE_ALLOWED_DOMAINS"
            v={state.env.allowed_domains.join(', ') || '(none)'}
          />
        </dl>
      </div>

      {/* Counts */}
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820]">
        <h3 className="text-sm font-semibold text-[#e2e8f0] mb-3">
          Registry
        </h3>
        <p className="text-xs text-[#7a8599]">
          {state.total_tools} of {state.env.max_tools} slots used
          {state.registry_full && (
            <span className="text-[#f87171] ml-2 font-medium">
              (FULL — new registrations will be rejected)
            </span>
          )}
        </p>
      </div>
    </div>
  );
}

function Pill({
  label,
  on,
  prominent,
}: {
  label: string;
  on: boolean;
  prominent?: boolean;
}) {
  const cls = on
    ? prominent
      ? 'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30'
      : 'bg-[#60a5fa]/15 text-[#60a5fa] border-[#60a5fa]/30'
    : 'bg-[#7a8599]/10 text-[#7a8599] border-[#7a8599]/30';
  return (
    <div className={`p-3 rounded border ${cls} text-center`}>
      <div className="text-xs uppercase tracking-wider opacity-80">{label}</div>
      <div className="text-sm font-semibold mt-1">{on ? 'on' : 'off'}</div>
    </div>
  );
}

function EnvRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-2 min-w-0">
      <code className="text-[#60a5fa] flex-shrink-0">{k}</code>
      <span className="text-[#7a8599]">=</span>
      <code className="text-[#cbd5e1] truncate">{v}</code>
    </div>
  );
}
