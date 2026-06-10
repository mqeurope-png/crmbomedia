"use client";

import type { ExternalSystem } from "../lib/integrationSettings";

type Tab = {
  system: ExternalSystem;
  label: string;
  count: number;
};

type Props = {
  tabs: Tab[];
  active: ExternalSystem;
  onChange: (system: ExternalSystem) => void;
};

/**
 * Horizontal tabs for the integrations admin page. With 12+ accounts
 * across four systems the previous "render everything stacked" layout
 * was unscrollable in practice; tabs collapse the noise to one system
 * at a time.
 */
export function IntegrationSystemTabs({ tabs, active, onChange }: Props) {
  return (
    <nav className="integration-tabs" role="tablist" aria-label="Sistemas">
      {tabs.map((tab) => {
        const isActive = tab.system === active;
        return (
          <button
            key={tab.system}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={`integration-tab${isActive ? " is-active" : ""}`}
            onClick={() => onChange(tab.system)}
          >
            <span>{tab.label}</span>
            <span className="integration-tab-badge" aria-hidden>
              {tab.count}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
