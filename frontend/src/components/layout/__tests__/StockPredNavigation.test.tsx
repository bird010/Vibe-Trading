import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../Layout";


const apiMock = vi.hoisted(() => ({
  listSessions: vi.fn(),
  deleteSession: vi.fn(),
  renameSession: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

describe("StockPred navigation", () => {
  beforeEach(() => {
    apiMock.listSessions.mockReset();
    apiMock.listSessions.mockResolvedValue([]);
    localStorage.removeItem("qa-sidebar");
  });

  it("renders and activates the StockPred sidebar item", async () => {
    render(
      <MemoryRouter initialEntries={["/stockpred"]}>
        <Routes>
          <Route path="*" element={<Layout />} />
        </Routes>
      </MemoryRouter>,
    );

    const link = await screen.findByRole("link", { name: /StockPred/i });
    expect(link).toHaveAttribute("href", "/stockpred");
    expect(link.className).toContain("text-primary");
  });
});
