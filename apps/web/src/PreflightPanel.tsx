import { useCallback, useEffect, useState } from "react";
import { API_URL } from "./api";
import type { PreflightCheck, SystemPreflight } from "./types";
import "./preflight.css";

const DEFAULT_TRANSLATION_MODEL = "translategemma:4b";

type MicrophoneState = "unchecked" | "checking" | "ready" | "error";

function checkSymbol(check: PreflightCheck) {
  if (check.status === "ready") return "✓";
  if (check.status === "warning") return "!";
  if (check.status === "info") return "i";
  return "×";
}

export function PreflightPanel() {
  const [open, setOpen] = useState(true);
  const [report, setReport] = useState<SystemPreflight | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [microphone, setMicrophone] = useState<MicrophoneState>("unchecked");
  const [microphoneDetail, setMicrophoneDetail] = useState("아직 확인하지 않았습니다.");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ translation_model: DEFAULT_TRANSLATION_MODEL });
      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);
      if (!response.ok) throw new Error(`시스템 점검 HTTP ${response.status}`);
      setReport((await response.json()) as SystemPreflight);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "시스템 점검에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function checkMicrophone() {
    setMicrophone("checking");
    setMicrophoneDetail("마이크 권한을 확인하는 중입니다…");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("이 브라우저는 오디오 입력 API를 지원하지 않습니다.");
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((device) => device.kind === "audioinput");
        if (inputs.length === 0) throw new Error("사용 가능한 오디오 입력 장치가 없습니다.");
        setMicrophone("ready");
        setMicrophoneDetail(`오디오 입력 ${inputs.length}개를 확인했습니다.`);
      } finally {
        stream.getTracks().forEach((track) => track.stop());
      }
    } catch (reason) {
      setMicrophone("error");
      setMicrophoneDetail(
        reason instanceof Error ? reason.message : "마이크 권한 또는 장치를 확인하지 못했습니다.",
      );
    }
  }

  const backendReady = report?.ready ?? false;
  const fullyReady = backendReady && microphone === "ready";
  const headline = fullyReady
    ? "사용 준비 완료"
    : backendReady
      ? "엔진 준비됨 · 마이크 확인 필요"
      : "설정 확인 필요";

  if (!open) {
    return (
      <button className={`preflight-pill ${backendReady ? "ready" : "attention"}`} onClick={() => setOpen(true)}>
        시스템 점검 · {backendReady ? "엔진 준비" : "확인 필요"}
      </button>
    );
  }

  return (
    <aside className="preflight-panel" aria-label="시스템 사전점검">
      <div className="preflight-titlebar">
        <div>
          <small>시작 전 점검</small>
          <strong>{headline}</strong>
        </div>
        <button className="preflight-close" onClick={() => setOpen(false)} aria-label="점검 패널 접기">−</button>
      </div>

      {error && <div className="preflight-error">{error}</div>}
      {report && (
        <>
          <div className="preflight-system">
            <span>{report.architecture}</span>
            {report.memory_gb !== null && <span>{report.memory_gb.toFixed(1)} GB RAM</span>}
            {report.disk_free_gb !== null && <span>{report.disk_free_gb.toFixed(1)} GB free</span>}
          </div>
          <div className="preflight-checks">
            {report.checks.map((check) => (
              <div className={`preflight-check ${check.status}`} key={check.id}>
                <i>{checkSymbol(check)}</i>
                <div>
                  <strong>{check.label}</strong>
                  <span>{check.summary}</span>
                  {check.recommendation && <small>{check.recommendation}</small>}
                </div>
              </div>
            ))}
            <div className={`preflight-check ${microphone === "ready" ? "ready" : microphone === "error" ? "error" : "info"}`}>
              <i>{microphone === "ready" ? "✓" : microphone === "error" ? "×" : "i"}</i>
              <div>
                <strong>마이크 / 오디오 입력</strong>
                <span>{microphoneDetail}</span>
              </div>
            </div>
          </div>
        </>
      )}

      <div className="preflight-actions">
        <button onClick={() => void load()} disabled={loading}>
          {loading ? "점검 중…" : "시스템 다시 점검"}
        </button>
        <button onClick={checkMicrophone} disabled={microphone === "checking"}>
          {microphone === "checking" ? "확인 중…" : "마이크 점검"}
        </button>
      </div>
    </aside>
  );
}
