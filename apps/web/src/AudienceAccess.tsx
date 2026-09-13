import { useEffect, useMemo, useState } from "react";
import QRCode from "qrcode";
import { API_URL } from "./api";
import { useI18n } from "./i18n";
import { PANEL_COPY } from "./panelCopy";
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
  const { locale } = useI18n();
  const copy = PANEL_COPY[locale].share;
  const [network, setNetwork] = useState<NetworkInfo | null>(null);
  const [mdns, setMdns] = useState<MdnsStatus | null>(null);
  const [selectedHost, setSelectedHost] = useState("");
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const networkRequest = fetch(`${API_URL}/api/v1/network`).then(async (response) => {
      if (!response.ok) throw new Error(copy.networkError);
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
  }, [copy.networkError]);

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
      .catch(() => setError(copy.qrError));
  }, [audienceUrl, copy.qrError]);

  async function copyValue(value: string) {
    if (value) await navigator.clipboard.writeText(value);
  }

  return (
    <div className="share-panel panel audience-access">
      <div className="audience-access-info">
        <span className="eyebrow">{copy.eyebrow}</span>
        <strong className="join-code">{joinCode}</strong>
        <span className="share-note">{copy.wifiHint}</span>
        {mdns?.ready && mdns.hostname && (
          <span className="share-note">{copy.readableAddress}: {mdns.hostname}</span>
        )}

        {hosts.length > 1 && (
          <label className="audience-host-select">
            {copy.network}
            <select value={selectedHost} onChange={(event) => setSelectedHost(event.target.value)}>
              {hosts.map((host) => (
                <option value={host} key={host}>
                  {host}{host === mdns?.hostname ? " · mDNS" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {!selectedHost && <div className="network-warning">{copy.noLan}</div>}
        {mdns?.error && <div className="network-warning">{copy.mdnsFallback}</div>}
        {error && <div className="network-warning">{error}</div>}

        <div className="share-links">
          <button className="secondary-button" onClick={() => copyValue(audienceUrl)} disabled={!audienceUrl}>
            {copy.copyAudience}
          </button>
          <button
            className="secondary-button"
            onClick={() => projectorUrl && window.open(projectorUrl, "_blank")}
            disabled={!projectorUrl}
          >
            {copy.openProjector}
          </button>
          <button className="secondary-button" onClick={() => copyValue(obsUrl)} disabled={!obsUrl}>
            {copy.copyObs}
          </button>
        </div>
      </div>

      <div className="qr-card">
        {qrDataUrl ? <img src={qrDataUrl} alt={copy.qrAlt} /> : <div className="qr-placeholder" />}
        <small>{audienceUrl || copy.checkingLan}</small>
      </div>
    </div>
  );
}
