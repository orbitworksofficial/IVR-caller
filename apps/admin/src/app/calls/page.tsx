"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import {
  Alert, EmptyState, LoadingState, Modal, Spinner, StatusBadge, formatDate,
} from "@/components/ui";
import { IconPhone, IconPlus, IconTrash } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type {
  Admin, Bot, CallCampaign, CallDetailResponse, CallRecord,
  ConsentRecord, DncEntry, PhoneNumber, VoiceProfile,
} from "@/lib/types";

type SubTab = "calls" | "numbers" | "profiles" | "compliance" | "campaigns";

export default function VoiceAndCallsPage() {
  const [subTab, setSubTab] = useState<SubTab>("calls");
  const [me, setMe] = useState<Admin | null>(null);
  const [bots, setBots] = useState<Bot[]>([]);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [consent, setConsent] = useState<ConsentRecord[]>([]);
  const [dnc, setDnc] = useState<DncEntry[]>([]);
  const [campaigns, setCampaigns] = useState<CallCampaign[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCall, setSelectedCall] = useState<CallDetailResponse | null>(null);

  // Forms
  const [newNumber, setNewNumber] = useState("");
  const [newNumberBot, setNewNumberBot] = useState("");
  const [profName, setProfName] = useState("");
  const [profVoiceId, setProfVoiceId] = useState("");
  const [consentPhone, setConsentPhone] = useState("");
  const [dncPhone, setDncPhone] = useState("");
  const [dncReason, setDncReason] = useState("");
  const [campName, setCampName] = useState("");
  const [campBot, setCampBot] = useState("");
  const [campReason, setCampReason] = useState("");
  const [campPhones, setCampPhones] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      api.me(),
      api.listBots(),
      api.listCalls(),
      api.listPhoneNumbers(),
      api.listVoiceProfiles(),
      api.listConsent(),
      api.listDnc(),
      api.listCampaigns(),
    ])
      .then(([admin, botList, callList, numList, profList, consentList, dncList, campList]) => {
        setMe(admin);
        setBots(botList);
        if (botList[0]) {
          setNewNumberBot(botList[0].id);
          setCampBot(botList[0].id);
        }
        setCalls(callList);
        setNumbers(numList);
        setProfiles(profList);
        setConsent(consentList);
        setDnc(dncList);
        setCampaigns(campList);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const canManage = me?.role === "admin" || !!me?.is_super_admin;

  async function openCall(callId: string) {
    try {
      const detail = await api.getCallDetail(callId);
      setSelectedCall(detail);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  async function handleAddNumber(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.addPhoneNumber({
        phone_number: newNumber.trim(),
        bot_id: newNumberBot || null,
        direction: "both",
        configure_twilio_webhooks: true,
      });
      setNumbers([created, ...numbers.filter((n) => n.id !== created.id)]);
      setNewNumber("");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAddProfile(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createVoiceProfile({
        name: profName.trim(),
        voice_id: profVoiceId.trim(),
      });
      setProfiles([created, ...profiles.filter((p) => p.id !== created.id)]);
      setProfName("");
      setProfVoiceId("");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleGrantConsent(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const rec = await api.grantConsent({
        phone_number: consentPhone.trim(),
        consent_type: "call",
        source: "crm_import",
      });
      setConsent([rec, ...consent.filter((c) => c.id !== rec.id)]);
      setConsentPhone("");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAddDnc(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const entry = await api.addDnc({
        phone_number: dncPhone.trim(),
        source: "internal",
        reason: dncReason.trim() || undefined,
      });
      setDnc([entry, ...dnc.filter((d) => d.id !== entry.id)]);
      setDncPhone("");
      setDncReason("");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateCampaign(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const nums = campPhones
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const created = await api.createCampaign({
        name: campName.trim(),
        bot_id: campBot,
        call_reason: campReason.trim() || undefined,
        phone_numbers: nums,
      });
      setCampaigns([created, ...campaigns]);
      setCampName("");
      setCampReason("");
      setCampPhones("");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleCampaign(camp: CallCampaign) {
    const next = camp.status === "active" ? "paused" : "active";
    try {
      const updated = await api.updateCampaign(camp.id, { status: next });
      setCampaigns(campaigns.map((c) => (c.id === camp.id ? { ...c, ...updated } : c)));
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  if (loading) {
    return (
      <Shell title="Voice & Calls">
        <LoadingState label="Loading voice gateway data…" />
      </Shell>
    );
  }

  return (
    <Shell
      title="Voice & Calls"
      subtitle="Manage inbound/outbound phone calls, Twilio numbers, voice profiles, consent/DNC, and campaigns."
    >
      {error && <Alert kind="error">{error}</Alert>}

      <div className="tabs">
        {([
          ["calls", `Call History (${calls.length})`],
          ["numbers", `Phone Numbers (${numbers.length})`],
          ["profiles", `Voice Profiles (${profiles.length})`],
          ["compliance", "Consent & DNC"],
          ["campaigns", `Campaigns (${campaigns.length})`],
        ] as [SubTab, string][]).map(([key, label]) => (
          <button
            key={key}
            className={`tab${subTab === key ? " active" : ""}`}
            onClick={() => setSubTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {subTab === "calls" && (
        <div className="card">
          {calls.length === 0 ? (
            <EmptyState
              icon={<IconPhone />}
              title="No calls recorded yet"
              text="Inbound and outbound voice calls will appear here with full transcripts, latency, and recordings."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Direction</th>
                    <th>From</th>
                    <th>To</th>
                    <th>Status</th>
                    <th>Outcome</th>
                    <th>Duration</th>
                    <th>Cost</th>
                    <th>Started</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {calls.map((c) => (
                    <tr key={c.id}>
                      <td><StatusBadge status={c.direction} /></td>
                      <td className="cell-strong">{c.from_number}</td>
                      <td>{c.to_number}</td>
                      <td><StatusBadge status={c.status} /></td>
                      <td>{c.outcome || "—"}</td>
                      <td>{c.duration_s != null ? `${c.duration_s}s` : "—"}</td>
                      <td>
                        {c.estimated_cost_usd != null
                          ? `$${Number(c.estimated_cost_usd).toFixed(4)}`
                          : "—"}
                      </td>
                      <td className="cell-muted">{formatDate(c.started_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => openCall(c.id)}
                        >
                          Transcript
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {subTab === "numbers" && (
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">Twilio Phone Numbers</div>
              <div className="card-desc">
                Map E.164 Twilio numbers to bots; webhooks are configured automatically.
              </div>
            </div>
          </div>
          <div className="card-body">
            {canManage && (
              <form
                onSubmit={handleAddNumber}
                style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}
              >
                <input
                  className="input"
                  style={{ maxWidth: 240 }}
                  placeholder="+14435550123"
                  value={newNumber}
                  onChange={(e) => setNewNumber(e.target.value)}
                  required
                />
                <select
                  className="select"
                  style={{ maxWidth: 240 }}
                  value={newNumberBot}
                  onChange={(e) => setNewNumberBot(e.target.value)}
                >
                  {bots.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
                <button className="btn btn-primary" disabled={busy || !newNumber.trim()}>
                  {busy ? <Spinner /> : <><IconPlus /> Add Number</>}
                </button>
              </form>
            )}

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Phone Number</th>
                    <th>Direction</th>
                    <th>Active</th>
                    <th>Added</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {numbers.map((n) => (
                    <tr key={n.id}>
                      <td className="cell-strong">{n.phone_number}</td>
                      <td>{n.direction}</td>
                      <td><StatusBadge status={n.is_active ? "active" : "paused"} /></td>
                      <td className="cell-muted">{formatDate(n.created_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        {canManage && (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={async () => {
                              await api.deletePhoneNumber(n.id);
                              setNumbers(numbers.filter((x) => x.id !== n.id));
                            }}
                          >
                            <IconTrash />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {subTab === "profiles" && (
        <div className="card">
          <div className="card-header">
            <div>
              <div className="card-title">ElevenLabs Voice Profiles</div>
              <div className="card-desc">Reusable TTS voices across your bots.</div>
            </div>
          </div>
          <div className="card-body">
            {canManage && (
              <form
                onSubmit={handleAddProfile}
                style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}
              >
                <input
                  className="input"
                  style={{ maxWidth: 220 }}
                  placeholder="Profile name (e.g. Sarah Warm)"
                  value={profName}
                  onChange={(e) => setProfName(e.target.value)}
                  required
                />
                <input
                  className="input"
                  style={{ maxWidth: 260 }}
                  placeholder="ElevenLabs Voice ID"
                  value={profVoiceId}
                  onChange={(e) => setProfVoiceId(e.target.value)}
                  required
                />
                <button className="btn btn-primary" disabled={busy}>
                  {busy ? <Spinner /> : <><IconPlus /> Save Profile</>}
                </button>
              </form>
            )}

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Voice ID</th>
                    <th>Model</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((p) => (
                    <tr key={p.id}>
                      <td className="cell-strong">{p.name}</td>
                      <td><code>{p.voice_id}</code></td>
                      <td>{p.model_id || "eleven_flash_v2_5"}</td>
                      <td style={{ textAlign: "right" }}>
                        {canManage && (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={async () => {
                              await api.deleteVoiceProfile(p.id);
                              setProfiles(profiles.filter((x) => x.id !== p.id));
                            }}
                          >
                            <IconTrash />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {subTab === "compliance" && (
        <div className="grid grid-2">
          <div className="card">
            <div className="card-header">
              <div>
                <div className="card-title">Outbound Call Consent</div>
                <div className="card-desc">Required before placing any outbound call.</div>
              </div>
            </div>
            <div className="card-body">
              <form onSubmit={handleGrantConsent} style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <input
                  className="input"
                  placeholder="+14435550123"
                  value={consentPhone}
                  onChange={(e) => setConsentPhone(e.target.value)}
                  required
                />
                <button className="btn btn-primary" disabled={busy}>Grant</button>
              </form>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Number</th><th>Source</th><th>Status</th><th></th></tr></thead>
                  <tbody>
                    {consent.map((c) => (
                      <tr key={c.id}>
                        <td className="cell-strong">{c.phone_number}</td>
                        <td>{c.source}</td>
                        <td>
                          <StatusBadge status={c.revoked_at ? "archived" : "active"} />
                        </td>
                        <td style={{ textAlign: "right" }}>
                          {!c.revoked_at && (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={async () => {
                                await api.revokeConsent(c.id);
                                setConsent(
                                  consent.map((x) =>
                                    x.id === c.id ? { ...x, revoked_at: new Date().toISOString() } : x
                                  )
                                );
                              }}
                            >
                              Revoke
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <div>
                <div className="card-title">Do-Not-Call (DNC) List</div>
                <div className="card-desc">Immediately blocks all outbound calls to these numbers.</div>
              </div>
            </div>
            <div className="card-body">
              <form onSubmit={handleAddDnc} style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <input
                  className="input"
                  placeholder="+14435550123"
                  value={dncPhone}
                  onChange={(e) => setDncPhone(e.target.value)}
                  required
                />
                <input
                  className="input"
                  placeholder="Reason (optional)"
                  value={dncReason}
                  onChange={(e) => setDncReason(e.target.value)}
                />
                <button className="btn btn-danger" disabled={busy}>Block</button>
              </form>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Number</th><th>Source</th><th>Reason</th><th></th></tr></thead>
                  <tbody>
                    {dnc.map((d) => (
                      <tr key={d.id}>
                        <td className="cell-strong">{d.phone_number}</td>
                        <td>{d.source}</td>
                        <td className="cell-muted">{d.reason || "—"}</td>
                        <td style={{ textAlign: "right" }}>
                          {canManage && (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={async () => {
                                await api.removeDnc(d.id);
                                setDnc(dnc.filter((x) => x.id !== d.id));
                              }}
                            >
                              <IconTrash />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {subTab === "campaigns" && (
        <div className="grid grid-2">
          {canManage && (
            <div className="card">
              <div className="card-header">
                <div>
                  <div className="card-title">Create Outbound Campaign</div>
                  <div className="card-desc">Numbers are checked against consent, DNC, and calling window.</div>
                </div>
              </div>
              <div className="card-body">
                <form onSubmit={handleCreateCampaign}>
                  <div className="field">
                    <label className="label">Campaign Name</label>
                    <input
                      className="input"
                      value={campName}
                      onChange={(e) => setCampName(e.target.value)}
                      placeholder="Q4 Discovery Follow-up"
                      required
                    />
                  </div>
                  <div className="field">
                    <label className="label">Bot</label>
                    <select
                      className="select"
                      value={campBot}
                      onChange={(e) => setCampBot(e.target.value)}
                    >
                      {bots.map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label className="label">Call Reason (injected into outbound prompt)</label>
                    <input
                      className="input"
                      value={campReason}
                      onChange={(e) => setCampReason(e.target.value)}
                      placeholder="Following up on your website inquiry"
                    />
                  </div>
                  <div className="field">
                    <label className="label">Phone Numbers (E.164, comma or newline separated)</label>
                    <textarea
                      className="textarea"
                      value={campPhones}
                      onChange={(e) => setCampPhones(e.target.value)}
                      placeholder="+14435550101&#10;+14435550102"
                    />
                  </div>
                  <button className="btn btn-primary" disabled={busy}>
                    {busy ? <Spinner /> : "Create Campaign"}
                  </button>
                </form>
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-header">
              <div>
                <div className="card-title">Campaigns</div>
                <div className="card-desc">Start or pause outbound dialing queues.</div>
              </div>
            </div>
            <div className="card-body">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Status</th>
                      <th>Contacts</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {campaigns.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <div className="cell-strong">{c.name}</div>
                          <div className="cell-muted">{c.call_reason || c.campaign_type}</div>
                        </td>
                        <td><StatusBadge status={c.status} /></td>
                        <td className="cell-muted">
                          {c.contact_counts
                            ? Object.entries(c.contact_counts)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join(", ") || "0"
                            : "—"}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          {canManage && c.status !== "completed" && (
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => toggleCampaign(c)}
                            >
                              {c.status === "active" ? "Pause" : "Start"}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedCall && (
        <Modal
          title={`Call ${selectedCall.call.from_number} → ${selectedCall.call.to_number}`}
          onClose={() => setSelectedCall(null)}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {selectedCall.call.call_summary && (
              <Alert kind="info">
                <strong>Summary:</strong> {selectedCall.call.call_summary}
              </Alert>
            )}
            {selectedCall.recordings.length > 0 && (
              <div style={{ fontSize: 13 }}>
                <strong>Recording:</strong>{" "}
                {selectedCall.recordings[0].storage_path ||
                  selectedCall.recordings[0].recording_url ||
                  selectedCall.recordings[0].status}
              </div>
            )}
            <div style={{ maxHeight: 360, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
              {selectedCall.turns.map((t) => (
                <div
                  key={t.turn_index}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 8,
                    background: t.speaker === "agent" ? "#F3F4F6" : "#FDF2F8",
                    alignSelf: t.speaker === "agent" ? "flex-start" : "flex-end",
                    maxWidth: "88%",
                    fontSize: 13,
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 11, marginBottom: 2 }}>
                    {t.speaker.toUpperCase()}
                    {t.latency_ms != null ? ` (${t.latency_ms} ms)` : ""}
                  </div>
                  <div>{t.text}</div>
                </div>
              ))}
            </div>
          </div>
        </Modal>
      )}
    </Shell>
  );
}
