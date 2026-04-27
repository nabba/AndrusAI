// Forge API — kept separate from queries.ts/endpoints.ts so the diff stays
// localized and other dashboard surfaces don't import forge state by accident.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  ForgeAuditLogResponse,
  ForgeState,
  ForgeToolDetailResponse,
  ForgeToolListResponse,
} from '../types/forge';

const F = '/api/forge';

export const forgeEndpoints = {
  state: () => `${F}/state`,
  tools: (status?: string, limit = 200) =>
    status
      ? `${F}/tools?status=${encodeURIComponent(status)}&limit=${limit}`
      : `${F}/tools?limit=${limit}`,
  tool: (id: string) => `${F}/tools/${encodeURIComponent(id)}`,
  toolKill: (id: string) => `${F}/tools/${encodeURIComponent(id)}/kill`,
  toolAuditRerun: (id: string) =>
    `${F}/tools/${encodeURIComponent(id)}/audit/rerun`,
  toolPromote: (id: string) => `${F}/tools/${encodeURIComponent(id)}/promote`,
  toolDemote: (id: string) => `${F}/tools/${encodeURIComponent(id)}/demote`,
  toolInvoke: (id: string) => `${F}/tools/${encodeURIComponent(id)}/invoke`,
  override: () => `${F}/settings/override`,
  auditLog: (limit = 200) => `${F}/audit-log?limit=${limit}`,
  toolAuditLog: (id: string, limit = 200) =>
    `${F}/audit-log/${encodeURIComponent(id)}?limit=${limit}`,
  register: () => `${F}/tools`,
  compositionAudit: () => `${F}/composition/audit`,
  compositions: (limit = 100) => `${F}/compositions?limit=${limit}`,
};

export const forgeKeys = {
  state: ['forge', 'state'] as const,
  tools: (status?: string) => ['forge', 'tools', status ?? 'all'] as const,
  tool: (id: string) => ['forge', 'tool', id] as const,
  auditLog: (limit: number) => ['forge', 'audit-log', limit] as const,
};

export function useForgeStateQuery() {
  return useQuery({
    queryKey: forgeKeys.state,
    queryFn: () => api<ForgeState>(forgeEndpoints.state()),
    refetchInterval: 5_000,
  });
}

export function useForgeToolsQuery(status?: string) {
  return useQuery({
    queryKey: forgeKeys.tools(status),
    queryFn: () => api<ForgeToolListResponse>(forgeEndpoints.tools(status)),
    refetchInterval: 10_000,
  });
}

export function useForgeToolQuery(id: string | undefined) {
  return useQuery({
    queryKey: forgeKeys.tool(id ?? ''),
    queryFn: () =>
      api<ForgeToolDetailResponse>(forgeEndpoints.tool(id as string)),
    enabled: Boolean(id),
    refetchInterval: 5_000,
  });
}

export function useForgeAuditLogQuery(limit = 200) {
  return useQuery({
    queryKey: forgeKeys.auditLog(limit),
    queryFn: () => api<ForgeAuditLogResponse>(forgeEndpoints.auditLog(limit)),
    refetchInterval: 10_000,
  });
}

export function useKillToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api<{ killed: boolean }>(forgeEndpoints.toolKill(id), {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: forgeKeys.tool(id) });
      qc.invalidateQueries({ queryKey: ['forge', 'tools'] });
      qc.invalidateQueries({ queryKey: forgeKeys.state });
    },
  });
}

export function useRerunAuditMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      api<{ tool_id: string; status: string }>(
        forgeEndpoints.toolAuditRerun(id),
        { method: 'POST' },
      ),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: forgeKeys.tool(id) });
      qc.invalidateQueries({ queryKey: ['forge', 'tools'] });
    },
  });
}

export function useSetOverrideMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { enabled?: boolean; dry_run?: boolean }) =>
      api<{ effective: ForgeState['effective'] }>(forgeEndpoints.override(), {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: forgeKeys.state });
    },
  });
}

export function useRegisterToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description?: string;
      source_type: 'declarative' | 'python_sandbox';
      source_code: string;
      capabilities?: string[];
      parameters?: Record<string, unknown>;
      returns?: Record<string, unknown>;
      domain_allowlist?: string[];
      generator?: Record<string, unknown>;
    }) =>
      api<{ tool_id: string; status: string }>(forgeEndpoints.register(), {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['forge', 'tools'] });
      qc.invalidateQueries({ queryKey: forgeKeys.state });
    },
  });
}

export type InvokeResult = {
  ok: boolean;
  result: unknown;
  shadow_result?: unknown;
  error: string | null;
  mode: string;
  shadow_mode: boolean;
  elapsed_ms: number;
  capability_used?: string | null;
  resolved_ip?: string | null;
  status_code?: number | null;
  note?: string;
};

export function useInvokeToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      params,
    }: {
      id: string;
      params: Record<string, unknown>;
    }) =>
      api<InvokeResult>(forgeEndpoints.toolInvoke(id), {
        method: 'POST',
        body: JSON.stringify({ params, caller_agent: 'ui' }),
      }),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: forgeKeys.tool(id) });
    },
  });
}

export function usePromoteMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      target,
      reason,
    }: {
      id: string;
      target: 'SHADOW' | 'CANARY' | 'ACTIVE';
      reason?: string;
    }) =>
      api<{ tool_id: string; status: string }>(
        forgeEndpoints.toolPromote(id),
        {
          method: 'POST',
          body: JSON.stringify({ target, reason: reason ?? '' }),
        },
      ),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: forgeKeys.tool(id) });
      qc.invalidateQueries({ queryKey: ['forge', 'tools'] });
      qc.invalidateQueries({ queryKey: forgeKeys.state });
    },
  });
}

export function useDemoteMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      target,
      reason,
    }: {
      id: string;
      target: 'SHADOW' | 'CANARY' | 'DEPRECATED';
      reason?: string;
    }) =>
      api<{ tool_id: string; status: string }>(
        forgeEndpoints.toolDemote(id),
        {
          method: 'POST',
          body: JSON.stringify({ target, reason: reason ?? '' }),
        },
      ),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: forgeKeys.tool(id) });
      qc.invalidateQueries({ queryKey: ['forge', 'tools'] });
      qc.invalidateQueries({ queryKey: forgeKeys.state });
    },
  });
}

export type CompositionRow = {
  id: number;
  composition_id: string;
  tool_ids: string[];
  aggregate_capabilities: string[];
  risk_score: number | string;
  verdict: 'allow' | 'block' | 'needs_human';
  judge_explanation: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};

export function useCompositionsQuery() {
  return useQuery({
    queryKey: ['forge', 'compositions'] as const,
    queryFn: () =>
      api<{ compositions: CompositionRow[]; count: number }>(
        forgeEndpoints.compositions(),
      ),
    refetchInterval: 15_000,
  });
}

export function useCompositionAuditMutation() {
  return useMutation({
    mutationFn: (body: {
      composition_id: string;
      tool_ids: string[];
      call_graph?: Record<string, unknown>;
    }) =>
      api<{
        verdict: 'allow' | 'block' | 'needs_human';
        risk_score: number;
        explanation: string;
        matched_pairs: Array<{
          name: string;
          capabilities: string[];
          explanation: string;
          risk_delta: number;
        }>;
        aggregate_capabilities: string[];
      }>(forgeEndpoints.compositionAudit(), {
        method: 'POST',
        body: JSON.stringify(body),
      }),
  });
}
