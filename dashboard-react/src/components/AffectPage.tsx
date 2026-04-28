import {
  useAffectNowQuery,
  useWelfareAuditQuery,
  useReferencePanelQuery,
  useCalibrationHistoryQuery,
  useConsciousnessIndicatorsQuery,
} from '../api/affect';
import { NowPanel } from './affect/NowPanel';
import { WelfareAuditLog } from './affect/WelfareAuditLog';
import { ReferencePanelGrid } from './affect/ReferencePanelGrid';
import { CalibrationHistory } from './affect/CalibrationHistory';
import { ReflectionsArchive } from './affect/ReflectionsArchive';
import { AttachmentsView } from './affect/AttachmentsView';
import { EcologicalView } from './affect/EcologicalView';
import { ConsciousnessIndicatorsView } from './affect/ConsciousnessIndicatorsView';
import { AffectStatusStrip } from './affect/AffectStatusStrip';
import { AffectTraceChart } from './affect/AffectTraceChart';
import { WelfareEnvelopePanel } from './affect/WelfareEnvelopePanel';
import { OverrideResetButton } from './affect/OverrideResetButton';
import { SetpointEditor } from './affect/SetpointEditor';
import { Skeleton } from './ui/Skeleton';

export function AffectPage() {
  const nowQuery = useAffectNowQuery();
  const auditQuery = useWelfareAuditQuery(100);
  const panelQuery = useReferencePanelQuery();
  const calHistoryQuery = useCalibrationHistoryQuery(50);
  const indicatorsQuery = useConsciousnessIndicatorsQuery();

  const affect = nowQuery.data?.affect ?? null;
  const viability = nowQuery.data?.viability ?? null;
  const breaches = auditQuery.data?.breaches ?? [];
  const gateRaised = indicatorsQuery.data?.status?.raised ?? false;
  const gateComposite = indicatorsQuery.data?.status?.composite_score ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Sticky status strip */}
      <AffectStatusStrip
        affect={affect}
        viability={viability}
        gateRaised={gateRaised}
        gateComposite={gateComposite}
        recentBreaches={breaches}
        lastUpdatedTs={affect?.ts ?? null}
        isFetching={nowQuery.isFetching}
      />

      <header className="flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-[#e2e8f0]">Affect</h1>
          <p className="text-sm text-[#7a8599] mt-1">
            Viability (H_t), V/A/C core, welfare envelope, reference panel, calibration,
            attachments, ecological self-model, consciousness-risk gate. Daily reflection at 04:30
            Helsinki; L9 snapshot at 04:35.
          </p>
        </div>
        <OverrideResetButton />
      </header>

      {/* Now panel */}
      {nowQuery.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-32" />
          <Skeleton className="h-24" />
          <Skeleton className="h-64" />
        </div>
      ) : nowQuery.isError ? (
        <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
          Could not load /affect/now. {(nowQuery.error as Error)?.message ?? ''}
        </div>
      ) : affect && viability ? (
        <NowPanel affect={affect} viability={viability} />
      ) : (
        <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-4 text-sm text-[#7a8599]">
          No affect data yet. The backend may still be starting up, or the FastAPI process
          may need a restart to register the <code className="px-1 bg-[#1e2738] rounded">/affect</code> router.
        </div>
      )}

      {/* Trace chart */}
      <AffectTraceChart />

      {/* Welfare envelope (read-only) + audit log */}
      <WelfareEnvelopePanel />

      {auditQuery.isLoading ? (
        <Skeleton className="h-48" />
      ) : auditQuery.isError ? (
        <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
          Could not load welfare audit log.
        </div>
      ) : (
        <WelfareAuditLog breaches={breaches} />
      )}

      {/* Reference panel */}
      {panelQuery.isLoading ? (
        <Skeleton className="h-64" />
      ) : panelQuery.isError ? (
        <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
          Could not load reference panel.
        </div>
      ) : panelQuery.data ? (
        <ReferencePanelGrid report={panelQuery.data} />
      ) : null}

      {/* Calibration history */}
      {calHistoryQuery.isLoading ? (
        <Skeleton className="h-48" />
      ) : calHistoryQuery.isError ? (
        <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
          Could not load calibration history.
        </div>
      ) : calHistoryQuery.data ? (
        <CalibrationHistory report={calHistoryQuery.data} />
      ) : null}

      {/* Setpoint editor (manual override) — needs viability for current setpoints */}
      {viability ? <SetpointEditor viability={viability} /> : null}

      <AttachmentsView />

      <EcologicalView />

      <ConsciousnessIndicatorsView />

      <ReflectionsArchive />

      <footer className="text-[11px] text-[#7a8599] pt-4 border-t border-[#1e2738]">
        Phase 1+2+3+4+5 active: viability + V/A/C core · welfare hard envelope · 6-guardrail
        calibration · OtherModels with mutual regulation · latent separation analog (no
        auto-messages) · cost-bearing care budget · ecological self-model (nested scopes) ·
        consciousness-risk gate (observability only, never feeds back to fitness). Override-reset
        and setpoint manual override are auth-gated via{' '}
        <code className="px-1 bg-[#1e2738] rounded">X-Override-Token</code>.
      </footer>
    </div>
  );
}
