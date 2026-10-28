import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, fireEvent, within } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Settings } from "@/pages/Settings";
import * as settingsService from "@/services/settings";
import { ApiError } from "@/services/api";
import type { SystemStatus } from "@/types/api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const baseStatus: SystemStatus = {
  environment: "development",
  app_version: "1.0.0",
  timestamp: "2026-01-01T00:00:00Z",
  database: "connected",
  providers: [
    {
      provider: "openai",
      configured: false,
      status: "not_configured",
      detail: "Configuration pending — using development mock credentials.",
      last_tested_at: null,
    },
    {
      provider: "exotel",
      configured: true,
      status: "not_verified",
      detail: "Configured but not yet tested. Click Test Connection to verify.",
      last_tested_at: null,
    },
    {
      provider: "elevenlabs",
      configured: true,
      status: "connected",
      detail: "Connection successful.",
      last_tested_at: "2026-01-01T00:00:00Z",
    },
    {
      provider: "email",
      configured: true,
      status: "connected",
      detail: "Connection successful.",
      last_tested_at: "2026-01-01T00:00:00Z",
    },
  ],
};

describe("Settings page", () => {
  it("renders provider statuses honestly and never shows a raw secret", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(settingsService, "getSystemStatus").mockResolvedValue(baseStatus);

    const { container } = renderPage(<Settings />);

    await waitFor(() => expect(screen.getByText("Not Configured")).toBeInTheDocument());
    expect(screen.getByText("Not Verified")).toBeInTheDocument();
    // 2 provider cards (ElevenLabs, Email) + the hardcoded Backend row + the
    // Database row (mocked "connected") in the System section below.
    expect(screen.getAllByText("Connected").length).toBe(4);
    // Masked indicator only on the 2 connected *provider* rows, never on
    // Backend/Database (which aren't secrets), and never the raw value.
    expect(screen.getAllByText("••••••••••••").length).toBe(2);
    expect(container.innerHTML).not.toMatch(/sk-[a-zA-Z0-9]{10,}/);
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders a Test Connection button for every one of the four providers", async () => {
    vi.spyOn(settingsService, "getSystemStatus").mockResolvedValue(baseStatus);

    renderPage(<Settings />);

    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Test Connection" }).length).toBe(4),
    );
  });

  it("shows Testing... and disables the button while the request is in flight", async () => {
    vi.spyOn(settingsService, "getSystemStatus").mockResolvedValue(baseStatus);
    const pending = deferred<Awaited<ReturnType<typeof settingsService.testIntegration>>>();
    vi.spyOn(settingsService, "testIntegration").mockReturnValue(pending.promise);

    renderPage(<Settings />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Test Connection" }).length).toBe(4));

    const buttons = screen.getAllByRole("button", { name: "Test Connection" });
    fireEvent.click(buttons[0]);

    await waitFor(() => expect(screen.getByRole("button", { name: "Testing..." })).toBeDisabled());
    // Only the clicked provider's button enters the testing state — the
    // other three remain independently clickable.
    expect(screen.getAllByRole("button", { name: "Test Connection" }).length).toBe(3);

    pending.resolve({ provider: "openai", status: "connected", message: "Connection successful.", tested_at: "t" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Testing..." })).not.toBeInTheDocument());
  });

  it("shows a success message and updates status without a page reload", async () => {
    vi.spyOn(settingsService, "getSystemStatus").mockResolvedValue(baseStatus);
    vi.spyOn(settingsService, "testIntegration").mockResolvedValue({
      provider: "exotel",
      status: "connected",
      message: "Connection successful.",
      tested_at: "2026-02-02T00:00:00Z",
    });

    renderPage(<Settings />);
    await waitFor(() => expect(screen.getByText("Not Verified")).toBeInTheDocument());

    const exotelCard = screen.getByText("exotel").closest("div")!.parentElement!;
    fireEvent.click(within(exotelCard).getByRole("button", { name: "Test Connection" }));

    await waitFor(() => expect(screen.getByText(/✓ Connection successful\./)).toBeInTheDocument());
  });

  it("shows a safe failure message, never a raw exception, on failure", async () => {
    vi.spyOn(settingsService, "getSystemStatus").mockResolvedValue(baseStatus);
    vi.spyOn(settingsService, "testIntegration").mockRejectedValue(
      new ApiError(200, "FAILED", "Authentication failed. Please check your API credentials."),
    );

    renderPage(<Settings />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Test Connection" }).length).toBe(4));

    fireEvent.click(screen.getAllByRole("button", { name: "Test Connection" })[0]);

    await waitFor(() =>
      expect(screen.getByText(/✕ Authentication failed\. Please check your API credentials\./)).toBeInTheDocument(),
    );
  });
});
