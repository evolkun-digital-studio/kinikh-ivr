import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Departments } from "@/pages/Departments";
import * as departmentsService from "@/services/departments";

describe("Departments page", () => {
  it("renders empty state without console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([]);

    renderPage(<Departments />);

    await waitFor(() => expect(screen.getByText("No departments yet")).toBeInTheDocument());
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("renders populated departments and opens the create modal", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(departmentsService, "listDepartments").mockResolvedValue([
      {
        id: "d1",
        name: "sales",
        display_name: "Sales",
        email: "sales@x.com",
        greeting: null,
        is_active: true,
        call_count: 3,
        lead_count: 2,
      },
    ]);

    renderPage(<Departments />);

    await waitFor(() => expect(screen.getByText("Sales")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /new department/i }));
    expect(screen.getByRole("heading", { name: "New Department" })).toBeInTheDocument();

    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
