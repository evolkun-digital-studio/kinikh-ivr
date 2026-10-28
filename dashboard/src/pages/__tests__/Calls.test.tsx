import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Calls } from "@/pages/Calls";
import * as callsService from "@/services/calls";
import * as departmentsService from "@/services/departments";

describe("Calls page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(callsService, "listCalls").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([]);

    renderPage(<Calls />);

    await waitFor(() => expect(screen.getByText("No calls yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders a populated table with filters", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(callsService, "listCalls").mockResolvedValue({
      items: [
        {
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
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([
      { id: "d1", name: "sales", display_name: "Sales", email: "s@x.com", greeting: null, is_active: true, call_count: 1, lead_count: 0 },
    ]);

    renderPage(<Calls />);

    await waitFor(() => expect(screen.getByText("+911111111111")).toBeInTheDocument());
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
