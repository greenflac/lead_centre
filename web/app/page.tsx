"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiBase, getCompanies, getLeads, getStats, isMock } from "../lib/api";
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
      // По умолчанию открыт самый горячий, а не самый свежий: список отсортирован по
      // приоритету, и открытая карточка обязана быть той, что стоит первой строкой.
      const order: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2, INVALID: 3 };
      const first = [...data].sort(
        (a, b) =>
          (order[a.tier] ?? 9) - (order[b.tier] ?? 9) ||
          new Date(b.received_at).getTime() - new Date(a.received_at).getTime(),
      )[0];
      setSelectedId((current) => current ?? first?.id ?? null);
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
          <span className="brand-name">SORP Lead Centre</span>
          <span className="brand-sub">inbound triage &amp; registry watchlist</span>
        </div>
      </header>

      {/* Один постоянный индикатор режима данных вместо пяти оговорок по экрану
          (02_references.md §6.2, образец — полоса тестового режима Stripe). */}
      <div className="provenance-strip">
        <div className="provenance-inner">
          {isMock ? (
            <>
              <span className="provenance-mode">Demo data</span>
              <span className="provenance-item">
                the requests are invented, the registry records are real
              </span>
              <details className="details details-inline">
                <summary className="details-summary">where it comes from</summary>
                <div className="details-body">
                  <div>
                    {stats ? stats.leads.synthetic : leads.length} invented requests —{" "}
                    <code>{stats?.leads.source ?? "data/inbound_seed.csv"}</code>
                  </div>
                  <div>
                    {companies.length} real registry records — <code>{stats?.companies.source ?? "gleif"}</code>
                  </div>
                  <div>price ranges come from a demo price list, not SORP&apos;s</div>
                  <div>facts come from an offline heuristic — no model is called in this mode</div>
                </div>
              </details>
            </>
          ) : (
            <>
              <span className="provenance-mode">Live backend</span>
              <span className="provenance-item">
                <code>{apiBase}</code> — facts extracted by the language model
              </span>
            </>
          )}
        </div>
      </div>

      <main className="shell">
        {/* Три числа, а не пять равновесных плиток (02_references.md §6.1). Watchlist уехал
            во вкладку Discovered, где он и живёт; режим данных — в полосу выше. */}
        <div className="stats">
          <div className="stat">
            <div className="stat-label">Requests scored</div>
            <div className="stat-value">
              {leads.length}
              {counts.decided ? (
                <span className="stat-sub"> · {counts.decided} decided</span>
              ) : null}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Hot, waiting for an answer</div>
            <div className="stat-value">
              {counts.byTier.HIGH}
              <span className="stat-sub">
                {" "}
                · medium {counts.byTier.MEDIUM} · low {counts.byTier.LOW}
              </span>
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Not scored — priority refused</div>
            <div className="stat-value">
              {counts.byTier.INVALID}
              <span className="stat-sub">
                {" "}
                ·{" "}
                {/* Три исхода сохранены дословно: «не смогли узнать» не сворачивается
                    в «ноль». Изменены только слова — имя эндпоинта и «нарушения
                    инварианта» менеджер не читает. */}
                {statsFailed
                  ? "could not read how many failed the engine's own checks"
                  : `${stats?.leads.violations ?? 0} failed the engine's own checks`}
              </span>
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
