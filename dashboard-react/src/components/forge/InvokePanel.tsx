import { useMemo, useState } from 'react';
import { useInvokeToolMutation } from '../../api/forge';
import type { InvokeResult } from '../../api/forge';
import type { ToolStatus } from '../../types/forge';

interface Props {
  toolId: string;
  status: ToolStatus;
  parameterSchema: Record<string, unknown>;
}

const INVOCABLE: ToolStatus[] = ['SHADOW', 'CANARY', 'ACTIVE', 'DEPRECATED'];

function paramType(spec: unknown): string {
  if (spec && typeof spec === 'object' && 'type' in spec) {
    return String((spec as { type: unknown }).type ?? 'string');
  }
  return 'string';
}

export function InvokePanel({ toolId, status, parameterSchema }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [lastResult, setLastResult] = useState<InvokeResult | null>(null);
  const invokeMut = useInvokeToolMutation();

  const fields = useMemo(() => {
    return Object.entries(parameterSchema || {}).map(([name, spec]) => ({
      name,
      type: paramType(spec),
    }));
  }, [parameterSchema]);

  const canInvoke = INVOCABLE.includes(status);

  if (!canInvoke) {
    return (
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820] text-sm text-[#7a8599]">
        Tool is <span className="font-mono">{status}</span> — cannot be
        invoked. Tools must reach SHADOW (semantic audit passed) before they
        can run.
      </div>
    );
  }

  function coerce(name: string, type: string): unknown {
    const raw = values[name];
    if (raw === undefined || raw === '') return undefined;
    if (type === 'number') return Number(raw);
    if (type === 'integer') return parseInt(raw, 10);
    if (type === 'boolean') return raw === 'true' || raw === '1';
    if (type === 'object' || type === 'array') {
      try {
        return JSON.parse(raw);
      } catch {
        return raw;
      }
    }
    return raw;
  }

  function onInvoke() {
    const params: Record<string, unknown> = {};
    for (const f of fields) {
      const v = coerce(f.name, f.type);
      if (v !== undefined) params[f.name] = v;
    }
    invokeMut.mutate(
      { id: toolId, params },
      { onSuccess: (data) => setLastResult(data) },
    );
  }

  const result = lastResult;

  return (
    <div className="p-4 rounded-lg border border-[#60a5fa]/30 bg-[#60a5fa]/5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#e2e8f0]">Invoke</h3>
        <span className="text-[10px] uppercase tracking-wider text-[#7a8599]">
          mode: {status.toLowerCase()}
        </span>
      </div>

      {fields.length === 0 ? (
        <p className="text-xs text-[#7a8599]">
          No parameters declared in the manifest. Click invoke to run with no inputs.
        </p>
      ) : (
        <div className="space-y-2">
          {fields.map((f) => (
            <div key={f.name} className="grid grid-cols-[140px_1fr] items-center gap-2">
              <label
                htmlFor={`param-${f.name}`}
                className="text-xs text-[#cbd5e1] font-mono"
                title={f.type}
              >
                {f.name}{' '}
                <span className="text-[#7a8599]">({f.type})</span>
              </label>
              <input
                id={`param-${f.name}`}
                type={f.type === 'number' || f.type === 'integer' ? 'number' : 'text'}
                value={values[f.name] ?? ''}
                onChange={(e) =>
                  setValues((v) => ({ ...v, [f.name]: e.target.value }))
                }
                className="bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa]"
              />
            </div>
          ))}
        </div>
      )}

      <button
        onClick={onInvoke}
        disabled={invokeMut.isPending}
        className="w-full px-4 py-2 rounded bg-[#60a5fa]/15 text-[#60a5fa] hover:bg-[#60a5fa]/25 border border-[#60a5fa]/30 text-sm font-medium transition-colors disabled:opacity-50"
      >
        {invokeMut.isPending ? 'Invoking...' : 'Invoke tool'}
      </button>

      {result && (
        <div
          className={`rounded border p-3 ${
            result.ok
              ? 'border-[#34d399]/30 bg-[#34d399]/5'
              : 'border-[#f87171]/30 bg-[#f87171]/5'
          }`}
        >
          <div className="flex items-center gap-3 text-xs">
            <span
              className={`font-medium ${
                result.ok ? 'text-[#34d399]' : 'text-[#f87171]'
              }`}
            >
              {result.ok ? 'ok' : 'error'}
            </span>
            <span className="text-[#7a8599]">mode: {result.mode}</span>
            {result.shadow_mode && (
              <span className="text-[#fbbf24]">SHADOW (result hidden from caller)</span>
            )}
            {result.status_code != null && (
              <span className="text-[#7a8599]">http {result.status_code}</span>
            )}
            <span className="text-[#7a8599]">{result.elapsed_ms}ms</span>
            {result.capability_used && (
              <span className="font-mono text-[#60a5fa]">{result.capability_used}</span>
            )}
            {result.resolved_ip && (
              <span className="font-mono text-[#7a8599]">→ {result.resolved_ip}</span>
            )}
          </div>
          {result.error && (
            <div className="mt-2 text-sm text-[#f87171]">{result.error}</div>
          )}
          {result.note && (
            <div className="mt-2 text-xs text-[#fbbf24]">{result.note}</div>
          )}
          {(result.result !== null || result.shadow_result !== null) && (
            <pre className="mt-3 p-3 rounded bg-[#0a0e14] text-xs text-[#cbd5e1] overflow-x-auto max-h-72">
              {JSON.stringify(
                result.shadow_mode ? result.shadow_result : result.result,
                null,
                2,
              )}
            </pre>
          )}
        </div>
      )}

      {invokeMut.isError && !result && (
        <div className="text-xs text-[#f87171]">
          Network error: {String(invokeMut.error)}
        </div>
      )}
    </div>
  );
}
