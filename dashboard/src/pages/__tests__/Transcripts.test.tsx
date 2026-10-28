import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Transcripts } from "@/pages/Transcripts";
import * as recordingsService from "@/services/recordings";

describe("Transcripts page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(recordingsService, "listRecordings").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });

    renderPage(<Transcripts />);

    await waitFor(() => expect(screen.getByText("No transcripts yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("opens the drawer and loads a transcript on row click", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
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
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(recordingsService, "getRecording").mockResolvedValue({
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
      transcript: "Caller: Hi\nAI: Hello",
      summary: "Greeting only",
      errors: null,
    });

    renderPage(<Transcripts />);

    await waitFor(() => expect(screen.getByText("+911111111111")).toBeInTheDocument());
    fireEvent.click(screen.getByText("+911111111111"));

    await waitFor(() => expect(screen.getByText("Greeting only")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
