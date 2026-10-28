import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Emails } from "@/pages/Emails";
import * as emailsService from "@/services/emails";

describe("Emails page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(emailsService, "listEmails").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });

    renderPage(<Emails />);

    await waitFor(() => expect(screen.getByText("No emails sent yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders populated emails including a failed one with an error message", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(emailsService, "listEmails").mockResolvedValue({
      items: [
        {
          id: "e1",
          recipient_email: "sales@x.com",
          subject: "New lead",
          status: "failed",
          sent_at: null,
          retry_count: 2,
          error_message: "SMTP timeout",
          created_at: "2026-01-01T00:00:00Z",
          lead: { id: "l1", name: "Jane", phone: "+911111111111", department_name: "Sales" },
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });

    renderPage(<Emails />);

    await waitFor(() => expect(screen.getByText("SMTP timeout")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
