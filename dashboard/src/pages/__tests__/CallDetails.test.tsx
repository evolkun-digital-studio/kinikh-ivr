import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { CallDetails } from "@/pages/CallDetails";
import * as callsService from "@/services/calls";

describe("CallDetails page", () => {
  it("renders full detail with transcript, lead, and cost, without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(callsService, "getCall").mockResolvedValue({
      id: "c1",
      call_sid: "CA1",
      caller_number: "+911111111111",
      callee_number: null,
      status: "completed",
      direction: "inbound",
      duration_seconds: 65,
      provider: "exotel",
      started_at: "2026-01-01T00:00:00Z",
      ended_at: "2026-01-01T00:01:05Z",
      created_at: "2026-01-01T00:00:00Z",
      lead: { id: "l1", name: "Jane", phone: "+911111111111", department_id: "d1", department_name: "Sales", language: "en" },
      transcript: "Caller: Hi\nAI: Hello, how can I help?",
      error_message: null,
      cost: {
        telephony_cost_usd: 0.01,
        openai_cost_usd: 0.02,
        voice_cost_usd: 0.03,
        email_cost_usd: 0.0001,
        total_cost_usd: 0.0601,
        duration_seconds: 65,
      },
      recording: { id: "r1", has_audio: false, status: "completed", duration: 65 },
    });

    renderPage(<CallDetails />, { route: "/calls/c1", path: "/calls/:id" });

    await waitFor(() => expect(screen.getByText("Hello, how can I help?")).toBeInTheDocument());
    expect(screen.getByText("$0.0601")).toBeInTheDocument();
    expect(screen.getByText("No recording available")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders empty states when no transcript, lead, recording, or cost exist", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(callsService, "getCall").mockResolvedValue({
      id: "c1",
      call_sid: "CA1",
      caller_number: "+911111111111",
      callee_number: null,
      status: "failed",
      direction: "inbound",
      duration_seconds: null,
      provider: "exotel",
      started_at: null,
      ended_at: null,
      created_at: "2026-01-01T00:00:00Z",
      lead: null,
      transcript: null,
      error_message: "Timeout",
      cost: null,
      recording: null,
    });

    renderPage(<CallDetails />, { route: "/calls/c1", path: "/calls/:id" });

    await waitFor(() => expect(screen.getByText("No transcript available")).toBeInTheDocument());
    expect(screen.getByText("No lead captured for this call")).toBeInTheDocument();
    expect(screen.getByText("No cost data recorded for this call")).toBeInTheDocument();
    expect(screen.getByText("Timeout")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
