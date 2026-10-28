import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Usage } from "@/pages/Usage";
import * as usageService from "@/services/usage";
import * as systemService from "@/services/system";

describe("Usage page", () => {
  it("renders empty state without console errors or division-by-zero artifacts", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(usageService, "getUsageSummary").mockResolvedValue({
      totals: {
        telephony_usd: 0,
        openai_usd: 0,
        voice_usd: 0,
        email_usd: 0,
        total_usd: 0,
        call_count: 0,
        cost_per_call_usd: 0,
      },
      by_provider: [],
    });
    vi.spyOn(usageService, "getUsageTimeseries").mockResolvedValue([]);
    vi.spyOn(systemService, "getSystemStatus").mockResolvedValue({
      environment: "development",
      app_version: "1.0.0",
      timestamp: "2026-01-01T00:00:00Z",
      database: "connected",
      providers: [
        { provider: "openai", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
        { provider: "exotel", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
        { provider: "elevenlabs", configured: true, status: "connected", detail: "Configured", last_tested_at: null },
        { provider: "email", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
      ],
    });

    renderPage(<Usage />);

    await waitFor(() => expect(screen.getByText("No API usage recorded yet")).toBeInTheDocument());
    expect(screen.getAllByText("$0.0000").length).toBeGreaterThan(0);
    expect(screen.getByText("No cost data yet")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("shows OpenAI and Exotel as Not Configured, ElevenLabs as Connected", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(usageService, "getUsageSummary").mockResolvedValue({
      totals: {
        telephony_usd: 0.1,
        openai_usd: 0.2,
        voice_usd: 0.05,
        email_usd: 0.001,
        total_usd: 0.351,
        call_count: 5,
        cost_per_call_usd: 0.0702,
      },
      by_provider: [
        { provider: "openai", requests: 5, total_tokens: 500, total_characters: 0, estimated_cost_usd: 0.2 },
      ],
    });
    vi.spyOn(usageService, "getUsageTimeseries").mockResolvedValue([{ date: "2026-01-01", cost_usd: 0.351 }]);
    vi.spyOn(systemService, "getSystemStatus").mockResolvedValue({
      environment: "development",
      app_version: "1.0.0",
      timestamp: "2026-01-01T00:00:00Z",
      database: "connected",
      providers: [
        { provider: "openai", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
        { provider: "exotel", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
        { provider: "elevenlabs", configured: true, status: "connected", detail: "Configured", last_tested_at: null },
        { provider: "email", configured: false, status: "not_configured", detail: "Configuration pending", last_tested_at: null },
      ],
    });

    renderPage(<Usage />);

    await waitFor(() => expect(screen.getAllByText("Not Configured").length).toBe(3));
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
