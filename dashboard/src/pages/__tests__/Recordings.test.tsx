import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Recordings } from "@/pages/Recordings";
import * as recordingsService from "@/services/recordings";

describe("Recordings page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(recordingsService, "listRecordings").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });

    renderPage(<Recordings />);

    await waitFor(() => expect(screen.getByText("No recordings yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders a row without audio and a row with audio (lazy player, not fetched on render)", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchSpy = vi.spyOn(global, "fetch");
    vi.spyOn(recordingsService, "listRecordings").mockResolvedValue({
      items: [
        {
          id: "r1",
          call_sid: "CA1",
          caller_number: "+911111111111",
          department: "Sales",
          language: "en",
          start_time: "2026-01-01T00:00:00Z",
          end_time: null,
          duration: 60,
          status: "completed",
          has_audio: false,
        },
        {
          id: "r2",
          call_sid: "CA2",
          caller_number: "+922222222222",
          department: "Support",
          language: "hi",
          start_time: "2026-01-01T00:00:00Z",
          end_time: null,
          duration: 45,
          status: "completed",
          has_audio: true,
        },
      ],
      total: 2,
      limit: 20,
      offset: 0,
    });

    renderPage(<Recordings />);

    await waitFor(() => expect(screen.getByText("No recording")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /play/i })).toBeInTheDocument();
    // Rendering the list must not eagerly download every row's audio blob.
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
    fetchSpy.mockRestore();
  });
});
