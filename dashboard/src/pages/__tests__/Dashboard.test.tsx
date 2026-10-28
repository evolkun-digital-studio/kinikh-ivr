import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Dashboard } from "@/pages/Dashboard";
import * as dashboardService from "@/services/dashboard";
import * as analyticsService from "@/services/analytics";
import type { DashboardSummary, AnalyticsOverview } from "@/types/api";

const emptySummary: DashboardSummary = {
  kpis: {
    total_calls: 0,
    completed_calls: 0,
    failed_calls: 0,
    total_leads: 0,
    today_calls: 0,
    today_leads: 0,
    total_cost_usd: 0,
    average_call_duration_seconds: null,
  },
  recent_calls: [],
  recent_leads: [],
};

const emptyAnalytics: AnalyticsOverview = {
  calls_over_time: [],
  leads_over_time: [],
  calls_by_department: [],
  calls_by_language: {},
  completed_vs_failed: {},
  email_status_breakdown: {},
  cost_over_time: [],
};

describe("Dashboard page", () => {
  it("renders empty state without console errors when the database is empty", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(dashboardService, "getDashboardSummary").mockResolvedValue(emptySummary);
    vi.spyOn(analyticsService, "getAnalyticsOverview").mockResolvedValue(emptyAnalytics);

    renderPage(<Dashboard />);

    await waitFor(() => expect(screen.getAllByText("No calls yet").length).toBeGreaterThan(0));
    expect(screen.getAllByText("No leads yet").length).toBeGreaterThan(0);
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders populated KPIs, recent calls and recent leads", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(dashboardService, "getDashboardSummary").mockResolvedValue({
      kpis: {
        total_calls: 12,
        completed_calls: 9,
        failed_calls: 3,
        total_leads: 5,
        today_calls: 2,
        today_leads: 1,
        total_cost_usd: 1.2345,
        average_call_duration_seconds: 87,
      },
      recent_calls: [
        {
          id: "c1",
          call_sid: "CA1",
          caller_number: "+911234567890",
          callee_number: null,
          status: "completed",
          direction: "inbound",
          duration_seconds: 90,
          provider: "exotel",
          started_at: "2026-01-01T00:00:00Z",
          ended_at: "2026-01-01T00:01:30Z",
          created_at: "2026-01-01T00:00:00Z",
          lead: {
            id: "l1",
            name: "Jane Doe",
            phone: "+911234567890",
            department_id: "d1",
            department_name: "Sales",
            language: "en",
          },
        },
      ],
      recent_leads: [
        {
          id: "l1",
          name: "Jane Doe",
          phone: "+911234567890",
          email: "jane@example.com",
          requirement: "Wants a quote",
          summary: null,
          language: "en",
          department_id: "d1",
          department: { id: "d1", name: "sales", display_name: "Sales" },
          call: { id: "c1", call_sid: "CA1", status: "completed" },
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.spyOn(analyticsService, "getAnalyticsOverview").mockResolvedValue(emptyAnalytics);

    renderPage(<Dashboard />);

    await waitFor(() => expect(screen.getByText("Jane Doe")).toBeInTheDocument());
    expect(screen.getAllByText("+911234567890").length).toBeGreaterThan(0);
    expect(screen.getByText("$1.2345")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
