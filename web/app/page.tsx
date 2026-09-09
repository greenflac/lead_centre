"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiBase, apiMode, getCompanies, getLeads, getStats, isMock } from "../lib/api";
import { ApiError, type Company, type Lead, type Stats } from "../lib/types";
import DiscoveredView from "../components/DiscoveredView";
import InboxView from "../components/InboxView";
import NewLeadView from "../components/NewLeadView";

type TabKey = "inbox" | "new" | "discovered";

const TABS: { key: TabKey; label: string }[] = [
  { key: "inbox", label: "Inbox" },
  { key: "new", label: "New request" },
  { key: "discovered", label: "Discovered" },
];

function toApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError("unknown", String(caught));
}

export default function Page() {
  const [tab, setTab] = useState<TabKey>("inbox");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsFailed, setStatsFailed] = useState(false);
  const [leadsError, setLeadsError] = useState<ApiError | null>(null);
  const [companiesError, setCompaniesError] = useState<ApiError | null>(null);
  const [loadingLeads, setLoadingLeads] = useState(true);
  const [loadingCompanies, setLoadingCompanies] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Wall clock is read after mount only: rendering it on the server would make the
  // "3 h ago" column differ between server and client markup.
  const [now, setNow] = useState<Date | null>(null);

  const loadLeads = useCallback(async () => {
    setLoadingLeads(true);
    setLeadsError(null);
    try {
      const data = await getLeads();
      setLeads(data);
      setSelectedId((current) => current ?? data[0]?.id ?? null);
    } catch (caught) {
      setLeadsError(toApiError(caught));
    } finally {
      setLoadingLeads(false);
    }
  }, []);

  const loadCompanies = useCallback(async () => {
    setLoadingCompanies(true);
    setCompaniesError(null);
    try {
      setCompanies(await getCompanies());
    } catch (caught) {
      setCompaniesError(toApiError(caught));
    } finally {
      setLoadingCompanies(false);
    }
  }, []);

  useEffect(() => {
    setNow(new Date());
    void loadLeads();
    void loadCompanies();
    getStats()
      .then((data) => {
        setStats(data);
        setStatsFailed(false);
      })
      .catch(() => {
        setStats(null);
        setStatsFailed(true);
      });
  }, [loadLeads, loadCompanies]);

  const onLeadChanged = useCallback((updated: Lead) => {
    setLeads((current) => {
      const index = current.findIndex((lead) => lead.id === updated.id);
      if (index === -1) return [updated, ...current];
      return [...current.slice(0, index), updated, ...current.slice(index + 1)];
    });
  }, []);

  const onCreated = useCallback((lead: Lead) => {
    setLeads((current) => [lead, ...current]);
  }, []);

  const counts = useMemo(() => {
    const byTier = { HIGH: 0, MEDIUM: 0, LOW: 0, INVALID: 0 } as Record<string, number>;
    for (const lead of leads) byTier[lead.tier] = (byTier[lead.tier] ?? 0) + 1;
    const decided = leads.filter((lead) => lead.status !== "new").length;
    return { byTier, decided };
  }, [leads]);

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-name">SORP Lead Centre</span>
            <span className="brand-sub">inbound triage &amp; registry watchlist</span>
          </div>
          <div className="topbar-spacer" />
          <span className={`mode-pill ${isMock ? "" : "live"}`} title={isMock ? "No backend attached: data generated from repository files by web/scripts/gen_mock.py" : `Live backend: ${apiBase}`}>
            {apiMode} data
          </span>
        </div>
      </header>

      <main className="shell">
        <div className="stats">
          <div className="stat">
            <div className="stat-label">Requests scored</div>
            <div className="stat-value">
              {leads.length} <small>{counts.decided ? `· ${counts.decided} decided` : "· none decided yet"}</small>
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">High / medium / low</div>
            <div className="stat-value">
              {counts.byTier.HIGH} / {counts.byTier.MEDIUM} / {counts.byTier.LOW}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Not scored (invariant)</div>
            <div className="stat-value">
              {counts.byTier.INVALID} <small>· {stats?.leads.violations ?? 0} violations</small>
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Watchlist companies</div>
            <div className="stat-value">
              {companies.length}{" "}
              <small>· {companies.filter((item) => item.tier === "HIGH").length} high</small>
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Data</div>
            <div className="stat-value">
              <small>
                {stats
                  ? `${stats.leads.synthetic} synthetic / ${stats.leads.real} real requests · ${stats.companies.source}`
                  : statsFailed
                    ? "counters unavailable — /stats did not answer"
                    : "loading…"}
              </small>
            </div>
          </div>
        </div>

        <nav className="tabs" role="tablist">
          {TABS.map((item) => (
            <button
              key={item.key}
              role="tab"
              className="tab"
              aria-selected={tab === item.key}
              onClick={() => setTab(item.key)}
            >
              {item.label}
              {item.key === "inbox" ? <span className="tab-count">{leads.length}</span> : null}
              {item.key === "discovered" ? <span className="tab-count">{companies.length}</span> : null}
            </button>
          ))}
        </nav>

        {tab === "inbox" ? (
          <InboxView
            leads={leads}
            loading={loadingLeads}
            error={leadsError}
            now={now}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onLeadChanged={onLeadChanged}
            onRetry={loadLeads}
          />
        ) : null}

        {tab === "new" ? <NewLeadView now={now} onCreated={onCreated} onLeadChanged={onLeadChanged} /> : null}

        {tab === "discovered" ? (
          <DiscoveredView
            companies={companies}
            loading={loadingCompanies}
            error={companiesError}
            onRetry={loadCompanies}
          />
        ) : null}
      </main>
    </>
  );
}
