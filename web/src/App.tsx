import { useEffect, useMemo, useState } from "react";

import {
  buildEvidence,
  getHealth,
  queryEvidence,
  runReplay,
  runResearch,
  validateOutcome
} from "./api/client";
import { AttributionPanel } from "./components/AttributionPanel/AttributionPanel";
import { DecisionPanel } from "./components/DecisionPanel/DecisionPanel";
import { EvidencePanel } from "./components/EvidencePanel/EvidencePanel";
import { ReplayPanel } from "./components/ReplayPanel/ReplayPanel";
import { StatusBar } from "./components/StatusBar/StatusBar";
import type {
  Evidence,
  Health,
  OutcomeInput,
  ProviderBatchUpload,
  ReplayReport,
  ResearchResult,
  ValidationResult
} from "./types";

function localClockValue() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [subject, setSubject] = useState("000001.SZ");
  const [asOf, setAsOf] = useState(localClockValue);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [evidenceState, setEvidenceState] =
    useState<"idle" | "loading" | "ready" | "empty" | "error">("idle");
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [research, setResearch] = useState<ResearchResult | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [replay, setReplay] = useState<ReplayReport | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validationLoading, setValidationLoading] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const asOfIso = useMemo(() => new Date(asOf).toISOString(), [asOf]);

  useEffect(() => {
    getHealth().then(setHealth).catch((error: unknown) => {
      setHealthError(error instanceof Error ? error.message : "无法连接本地 API");
    });
  }, []);

  const loadEvidence = async () => {
    setEvidenceState("loading");
    setEvidenceError(null);
    setResearch(null);
    setValidation(null);
    try {
      const items = await queryEvidence(asOfIso, subject);
      setEvidence(items);
      setEvidenceState(items.length ? "ready" : "empty");
    } catch (error) {
      setEvidenceState("error");
      setEvidenceError(error instanceof Error ? error.message : "证据查询失败");
    }
  };

  const importBatch = async (file: File) => {
    setEvidenceState("loading");
    setEvidenceError(null);
    try {
      const upload = JSON.parse(await file.text()) as ProviderBatchUpload;
      const items = await buildEvidence(upload);
      setEvidence(items);
      setEvidenceState(items.length ? "ready" : "empty");
    } catch (error) {
      setEvidenceState("error");
      setEvidenceError(error instanceof Error ? error.message : "批次导入失败");
    }
  };

  const startResearch = async () => {
    setResearchLoading(true);
    setResearchError(null);
    try {
      setResearch(await runResearch({ subject, asOf: asOfIso, evidence }));
    } catch (error) {
      setResearchError(error instanceof Error ? error.message : "研究运行失败");
    } finally {
      setResearchLoading(false);
    }
  };

  const startValidation = async (outcome: OutcomeInput) => {
    if (!research) return;
    setValidationLoading(true);
    setValidationError(null);
    try {
      setValidation(await validateOutcome(research, outcome));
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : "结果归因失败");
    } finally {
      setValidationLoading(false);
    }
  };

  const startReplay = async (file: File) => {
    setReplayLoading(true);
    setReplayError(null);
    try {
      setReplay(await runReplay(JSON.parse(await file.text()) as unknown));
    } catch (error) {
      setReplayError(error instanceof Error ? error.message : "回放验证失败");
    } finally {
      setReplayLoading(false);
    }
  };

  return (
    <main>
      <header className="masthead">
        <div>
          <p className="product-mark">OPENALPHA / CN</p>
          <h1>证据先于观点</h1>
          <p>面向中国 A 股的时间点一致、多智能体研究工作台</p>
        </div>
        <div className="clock-card">
          <span>研究时钟</span>
          <strong>{asOf.replace("T", " ")}</strong>
          <small>本地时区 · 仅使用当时可见信息</small>
        </div>
      </header>
      <StatusBar health={health} error={healthError} />
      <div className="workbench-grid">
        <EvidencePanel
          subject={subject}
          asOf={asOf}
          evidence={evidence}
          state={evidenceState}
          error={evidenceError}
          onSubjectChange={setSubject}
          onAsOfChange={setAsOf}
          onQuery={loadEvidence}
          onImport={importBatch}
        />
        <DecisionPanel
          evidence={evidence}
          result={research}
          loading={researchLoading}
          error={researchError}
          onRun={startResearch}
        />
      </div>
      <ReplayPanel
        report={replay}
        loading={replayLoading}
        error={replayError}
        onRun={startReplay}
      />
      <AttributionPanel
        research={research}
        result={validation}
        loading={validationLoading}
        error={validationError}
        asOf={asOf}
        onRun={startValidation}
      />
      <footer className="legal-note">
        OpenAlpha CN 仅用于研究、教学与复盘，不构成投资建议，不连接实盘下单。
      </footer>
    </main>
  );
}
