import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { LeadDetails } from "@/pages/LeadDetails";
import * as leadsService from "@/services/leads";

describe("LeadDetails page", () => {
  it("renders full detail with email logs, without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(leadsService, "getLead").mockResolvedValue({
      id: "l1",
      name: "Jane",
      phone: "+911111111111",
      email: "jane@example.com",
      requirement: "Quote",
      summary: "Wants pricing",
      language: "en",
      department_id: "d1",
      department: { id: "d1", name: "sales", display_name: "Sales" },
      call: { id: "c1", call_sid: "CA1", status: "completed" },
      created_at: "2026-01-01T00:00:00Z",
      additional_notes: null,
      raw_transcript: "Caller: Hi\nAI: Hello",
      email_logs: [
        { id: "e1", recipient_email: "sales@x.com", status: "sent", sent_at: "2026-01-01T00:00:00Z", retry_count: 0, error_message: null },
      ],
    });

    renderPage(<LeadDetails />, { route: "/leads/l1", path: "/leads/:id" });

    await waitFor(() => expect(screen.getByText("sales@x.com")).toBeInTheDocument());
    expect(screen.getByText("Hello")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders empty states when no call or emails exist", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(leadsService, "getLead").mockResolvedValue({
      id: "l1",
      name: null,
      phone: "+911111111111",
      email: null,
      requirement: null,
      summary: null,
      language: null,
      department_id: null,
      department: null,
      call: null,
      created_at: "2026-01-01T00:00:00Z",
      additional_notes: null,
      raw_transcript: null,
      email_logs: [],
    });

    renderPage(<LeadDetails />, { route: "/leads/l1", path: "/leads/:id" });

    await waitFor(() => expect(screen.getByText("No linked call")).toBeInTheDocument());
    expect(screen.getByText("No transcript available")).toBeInTheDocument();
    expect(screen.getByText("No emails sent for this lead")).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
