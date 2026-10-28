import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Analytics } from "@/pages/Analytics";
import * as analyticsService from "@/services/analytics";

describe("Analytics page", () => {
  it("renders every chart's empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(analyticsService, "getAnalyticsOverview").mockResolvedValue({
      calls_over_time: [],
      leads_over_time: [],
      calls_by_department: [],
      calls_by_language: {},
      completed_vs_failed: {},
      email_status_breakdown: {},
      cost_over_time: [],
    });

    renderPage(<Analytics />);

    await waitFor(() => expect(screen.getAllByText("No calls yet").length).toBeGreaterThan(0));
    expect(screen.getByText("No completed or failed calls yet")).toBeInTheDocument();
    expect(screen.getByText("No emails sent yet")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders populated charts including status breakdowns", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(analyticsService, "getAnalyticsOverview").mockResolvedValue({
      calls_over_time: [{ date: "2026-01-01", count: 3 }],
      leads_over_time: [{ date: "2026-01-01", count: 2 }],
      calls_by_department: [{ department_id: "d1", department_name: "Sales", count: 3 }],
      calls_by_language: { en: 2, hi: 1 },
      completed_vs_failed: { completed: 2, failed: 1 },
      email_status_breakdown: { sent: 1, failed: 1 },
      cost_over_time: [{ date: "2026-01-01", cost_usd: 0.05 }],
    });

    renderPage(<Analytics />);

    await waitFor(() => expect(screen.getByText("Completed vs Failed Calls")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
