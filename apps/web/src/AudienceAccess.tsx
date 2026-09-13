import { useEffect, useMemo, useState } from "react";
import QRCode from "qrcode";
import { API_URL } from "./api";
import type { NetworkInfo } from "./types";

interface AudienceAccessProps {
  joinCode: string;
  targetLanguage: string;
}

interface MdnsStatus {
  hostname: string | null;
  ready: boolean;
  error: string | null;
}

function isLoopback(hostname: string) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

export function AudienceAccess({ joinCode, targetLanguage }: AudienceAccessProps) {
  const [network, setNetwork] = useState<NetworkInfo | null>(null);
  const [mdns, setMdns] = useState<MdnsStatus | null>(null);
  const [selectedHost, setSelectedHost] = useState("");
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const networkRequest = fetch(`${API_URL}/api/v1/network`).then(async (response) => {
      if (!response.ok) throw new Error("LAN 주소를 확인하지 못했습니다.");
      return (await response.json()) as NetworkInfo;
    });
    const mdnsRequest = fetch(`${API_URL}/api/v1/network/mdns`)
      .then(async (response) => {
        if (!response.ok) return null;
        return (await response.json()) as MdnsStatus;
      })
      .catch(() => null);

    Promise.all([networkRequest, mdnsRequest])
      .then(([networkValue, mdnsValue]) => {
        setNetwork(networkValue);
        setMdns(mdnsValue);
        const browserHost = window.location.hostname;
        const preferred = !isLoopback(browserHost)
          ? browserHost
          : mdnsValue?.ready && mdnsValue.hostname
            ? mdnsValue.hostname
            : networkValue.addresses[0] || "";
        setSelectedHost(preferred);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const frontendPort = network?.frontend_port ?? Number(window.location.port || 80);
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const baseUrl = selectedHost ? `${protocol}//${selectedHost}:${frontendPort}` : "";
  const audienceUrl = baseUrl
    ? `${baseUrl}/audience/${encodeURIComponent(joinCode)}?lang=${encodeURIComponent(targetLanguage)}`
    : "";
  const projectorUrl = baseUrl
    ? `${baseUrl}/display/${encodeURIComponent(joinCode)}?mode=projector&lang=${encodeURIComponent(targetLanguage)}`
    : "";
  const obsUrl = baseUrl
    ? `${baseUrl}/display/${encodeURIComponent(joinCode)}?mode=obs&lang=${encodeURIComponent(targetLanguage)}`
    : "";

  const hosts = useMemo(() => {
    const browserHost = window.location.hostname;
    const values: string[] = [];
    if (mdns?.ready && mdns.hostname) values.push(mdns.hostname);
    for (const address of network?.addresses ?? []) {
      if (!values.includes(address)) values.push(address);
    }
    if (!isLoopback(browserHost) && !values.includes(browserHost)) values.unshift(browserHost);
    return values;
  }, [mdns, network]);

  useEffect(() => {
    if (!audienceUrl) {
      setQrDataUrl("");
      return;
    }
    QRCode.toDataURL(audienceUrl, {
      width: 240,
      margin: 1,
      errorCorrectionLevel: "M",
    })
      .then(setQrDataUrl)
      .catch(() => setError("QR 코드를 생성하지 못했습니다."));
  }, [audienceUrl]);

  async function copy(value: string) {
    if (value) await navigator.clipboard.writeText(value);
  }

  return (
    <div className="share-panel panel audience-access">
      <div className="audience-access-info">
        <span className="eyebrow">Audience access</span>
        <strong className="join-code">{joinCode}</strong>
        <span className="share-note">청중은 같은 Wi-Fi에서 QR을 스캔하면 됩니다.</span>
        {mdns?.ready && mdns.hostname && (
          <span className="share-note">사람이 읽기 쉬운 주소: {mdns.hostname}</span>
        )}

        {hosts.length > 1 && (
          <label className="audience-host-select">
            접속 네트워크
            <select value={selectedHost} onChange={(event) => setSelectedHost(event.target.value)}>
              {hosts.map((host) => (
                <option value={host} key={host}>
                  {host}{host === mdns?.hostname ? " · mDNS" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {!selectedHost && (
          <div className="network-warning">
            사용 가능한 LAN 주소를 찾지 못했습니다. Wi-Fi/Ethernet 연결을 확인하세요.
          </div>
        )}
        {mdns?.error && (
          <div className="network-warning">mDNS를 사용할 수 없어 IP 주소로 공유합니다.</div>
        )}
        {error && <div className="network-warning">{error}</div>}

        <div className="share-links">
          <button className="secondary-button" onClick={() => copy(audienceUrl)} disabled={!audienceUrl}>
            청중 링크 복사
          </button>
          <button
            className="secondary-button"
            onClick={() => projectorUrl && window.open(projectorUrl, "_blank")}
            disabled={!projectorUrl}
          >
            프로젝터 열기
          </button>
          <button className="secondary-button" onClick={() => copy(obsUrl)} disabled={!obsUrl}>
            OBS URL 복사
          </button>
        </div>
      </div>

      <div className="qr-card">
        {qrDataUrl ? <img src={qrDataUrl} alt="Audience join QR code" /> : <div className="qr-placeholder" />}
        <small>{audienceUrl || "LAN 주소 확인 중"}</small>
      </div>
    </div>
  );
}
