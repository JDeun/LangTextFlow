import { useCallback, useEffect, useState } from "react";
import { API_URL } from "./api";
import type { ProductPreset, RecommendedConfiguration, SystemPreflight } from "./types";
import "./onboarding.css";

const LANGUAGES = [
  ["ko", "한국어"],
  ["en", "English"],
  ["ja", "日本語"],
  ["zh", "中文"],
] as const;

const PRESETS: Array<[ProductPreset, string]> = [
  ["general", "일반"],
  ["church", "교회 / 선교 집회"],
  ["conference", "컨퍼런스"],
  ["lecture", "강의"],
];

interface OnboardingWizardProps {
  open: boolean;
  engine: string;
  translationProvider: string;
  translationModel: string;
  sourceLanguage: string;
  targetLanguage: string;
  preset: ProductPreset;
  onEngineChange: (value: string) => void;
  onTranslationProviderChange: (value: string) => void;
  onTranslationModelChange: (value: string) => void;
  onSourceLanguageChange: (value: string) => void;
  onTargetLanguageChange: (value: string) => void;
  onPresetChange: (value: ProductPreset) => void;
  onApplyRecommendation: (configuration: RecommendedConfiguration) => void;
  onComplete: () => void;
  onClose: () => void;
}

type MicrophoneState = "unchecked" | "checking" | "ready" | "error";

export function OnboardingWizard({
  open,
  engine,
  translationProvider,
  translationModel,
  sourceLanguage,
  targetLanguage,
  preset,
  onEngineChange,
  onTranslationProviderChange,
  onTranslationModelChange,
  onSourceLanguageChange,
  onTargetLanguageChange,
  onPresetChange,
  onApplyRecommendation,
  onComplete,
  onClose,
}: OnboardingWizardProps) {
  const [step, setStep] = useState(0);
  const [report, setReport] = useState<SystemPreflight | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [microphone, setMicrophone] = useState<MicrophoneState>("unchecked");
  const [microphoneDetail, setMicrophoneDetail] = useState("아직 확인하지 않았습니다.");

  const loadPreflight = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        engine,
        translation_provider: translationProvider,
      });
      if (translationProvider === "ollama" && translationModel.trim()) {
        query.set("translation_model", translationModel.trim());
      }
      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);
      if (!response.ok) throw new Error(`시스템 점검 HTTP ${response.status}`);
      setReport((await response.json()) as SystemPreflight);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "시스템 점검에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }, [engine, translationModel, translationProvider]);

  useEffect(() => {
    if (!open || step !== 1) return;
    void loadPreflight();
  }, [loadPreflight, open, step]);

  useEffect(() => {
    if (open) setStep(0);
  }, [open]);

  if (!open) return null;

  async function checkMicrophone() {
    setMicrophone("checking");
    setMicrophoneDetail("마이크 권한과 입력 장치를 확인하는 중입니다…");
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
        reason instanceof Error ? reason.message : "마이크를 확인하지 못했습니다.",
      );
    }
  }

  function finish() {
    onComplete();
    setStep(0);
  }

  const steps = ["시작", "시스템", "마이크", "언어", "완료"];

  return (
    <div className="onboarding-backdrop" role="presentation">
      <section className="onboarding-dialog" role="dialog" aria-modal="true" aria-label="초기 설정">
        <header className="onboarding-header">
          <div>
            <small>LangTextFlow 초기 설정</small>
            <strong>{steps[step]}</strong>
          </div>
          <button onClick={onClose} aria-label="초기 설정 닫기">×</button>
        </header>

        <div className="onboarding-progress" aria-label="초기 설정 진행률">
          {steps.map((label, index) => (
            <i
              key={label}
              className={index <= step ? "active" : ""}
              title={label}
            />
          ))}
        </div>

        <div className="onboarding-body">
          {step === 0 && (
            <div className="onboarding-copy">
              <h2>실시간 자막을 시작하기 전에 필요한 항목을 확인합니다.</h2>
              <p>
                음성 인식, 로컬 번역, 마이크와 기본 언어를 순서대로 점검합니다.
                설치되어 있는 구성요소를 기준으로 안전한 기본 설정도 제안합니다.
              </p>
              <div className="onboarding-note">
                점검 과정은 모델이나 드라이버를 임의로 설치하지 않습니다.
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="onboarding-copy">
              <h2>시스템 구성</h2>
              {loading && <p>로컬 ASR/번역 환경을 확인하는 중입니다…</p>}
              {error && <div className="onboarding-error">{error}</div>}
              {report && (
                <>
                  <div className={`onboarding-status ${report.ready ? "ready" : "attention"}`}>
                    <strong>{report.ready ? "현재 설정으로 실행 가능" : "현재 설정에 준비되지 않은 항목이 있습니다."}</strong>
                    <span>
                      {report.architecture}
                      {report.memory_gb !== null ? ` · ${report.memory_gb.toFixed(1)} GB RAM` : ""}
                    </span>
                  </div>
                  {report.recommended.engine && (
                    <div className="onboarding-recommendation">
                      <small>권장 구성</small>
                      <strong>
                        {report.recommended.engine} · {report.recommended.translation_provider}
                        {report.recommended.translation_model
                          ? ` / ${report.recommended.translation_model}`
                          : ""}
                      </strong>
                      <ul>
                        {report.recommended.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                      </ul>
                      <button onClick={() => onApplyRecommendation(report.recommended)}>
                        권장 구성 적용
                      </button>
                    </div>
                  )}
                  <div className="onboarding-check-list">
                    {report.checks.map((check) => (
                      <div key={check.id} className={check.status}>
                        <b>{check.status === "ready" ? "✓" : check.status === "warning" ? "!" : "×"}</b>
                        <span>
                          <strong>{check.label}</strong>
                          <small>{check.summary}</small>
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="onboarding-copy">
              <h2>마이크 / 오디오 입력</h2>
              <p>
                브라우저가 실제 입력 장치를 사용할 수 있는지 확인합니다. 권한 확인 후 테스트 스트림은 즉시 종료합니다.
              </p>
              <div className={`onboarding-status ${microphone === "ready" ? "ready" : microphone === "error" ? "attention" : ""}`}>
                <strong>
                  {microphone === "ready"
                    ? "마이크 준비 완료"
                    : microphone === "checking"
                      ? "확인 중"
                      : microphone === "error"
                        ? "마이크 확인 실패"
                        : "마이크 확인 필요"}
                </strong>
                <span>{microphoneDetail}</span>
              </div>
              <button className="onboarding-primary" onClick={checkMicrophone} disabled={microphone === "checking"}>
                {microphone === "checking" ? "확인 중…" : "마이크 점검"}
              </button>
            </div>
          )}

          {step === 3 && (
            <div className="onboarding-copy">
              <h2>기본 사용 설정</h2>
              <div className="onboarding-grid">
                <label>
                  입력 언어
                  <select value={sourceLanguage} onChange={(event) => onSourceLanguageChange(event.target.value)}>
                    {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                  </select>
                </label>
                <label>
                  자막 언어
                  <select value={targetLanguage} onChange={(event) => onTargetLanguageChange(event.target.value)}>
                    {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                  </select>
                </label>
                <label>
                  사용 목적
                  <select value={preset} onChange={(event) => onPresetChange(event.target.value as ProductPreset)}>
                    {PRESETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label>
                  음성 인식
                  <select value={engine} onChange={(event) => onEngineChange(event.target.value)}>
                    <option value="auto">Auto</option>
                    <option value="vibevoice">VibeVoice Streaming</option>
                    <option value="faster-whisper">faster-whisper</option>
                    <option value="mock">Demo engine</option>
                  </select>
                </label>
                <label>
                  번역
                  <select
                    value={translationProvider}
                    onChange={(event) => onTranslationProviderChange(event.target.value)}
                  >
                    <option value="ollama">Ollama</option>
                    <option value="none">번역 사용 안 함</option>
                    {engine === "mock" && <option value="demo">Demo translator</option>}
                  </select>
                </label>
                {translationProvider === "ollama" && (
                  <label>
                    번역 모델
                    <input
                      value={translationModel}
                      onChange={(event) => onTranslationModelChange(event.target.value)}
                    />
                  </label>
                )}
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="onboarding-copy onboarding-finish">
              <div className="onboarding-finish-mark">✓</div>
              <h2>초기 설정이 완료되었습니다.</h2>
              <p>
                세션 이름과 발표자, 필요 용어를 입력한 뒤 자막을 시작할 수 있습니다.
                시스템 상태는 운영자 화면의 사전점검 패널에서 언제든 다시 확인할 수 있습니다.
              </p>
              <dl>
                <div><dt>음성 인식</dt><dd>{engine}</dd></div>
                <div><dt>번역</dt><dd>{translationProvider}</dd></div>
                <div><dt>언어</dt><dd>{sourceLanguage.toUpperCase()} → {targetLanguage.toUpperCase()}</dd></div>
              </dl>
            </div>
          )}
        </div>

        <footer className="onboarding-footer">
          <button onClick={step === 0 ? onClose : () => setStep((value) => Math.max(0, value - 1))}>
            {step === 0 ? "나중에" : "이전"}
          </button>
          {step < 4 ? (
            <button className="onboarding-primary" onClick={() => setStep((value) => Math.min(4, value + 1))}>
              다음
            </button>
          ) : (
            <button className="onboarding-primary" onClick={finish}>시작하기</button>
          )}
        </footer>
      </section>
    </div>
  );
}
