import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderPage } from "@/test/renderWithProviders";
import { Login } from "@/pages/Login";

describe("Login page", () => {
  it("renders the login form without console errors", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    renderPage(<Login />);

    expect(screen.getByText("Kinikh IVR")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("admin@company.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
