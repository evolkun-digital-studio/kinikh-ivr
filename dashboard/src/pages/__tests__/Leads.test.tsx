import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Leads } from "@/pages/Leads";
import * as leadsService from "@/services/leads";
import * as departmentsService from "@/services/departments";

describe("Leads page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(leadsService, "listLeads").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([]);

    renderPage(<Leads />);

    await waitFor(() => expect(screen.getByText("No leads yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders populated leads with missing optional fields handled", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(leadsService, "listLeads").mockResolvedValue({
      items: [
        {
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
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([]);

    renderPage(<Leads />);

    await waitFor(() => expect(screen.getByText("+911111111111")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
