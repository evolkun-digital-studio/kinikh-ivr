import { ReactNode, useState } from "react";
import { useLocation } from "react-router-dom";
import { SidebarContent } from "@/components/layout/Sidebar";
import { NAV_GROUPS } from "@/components/navigation/navConfig";
import { IconMenu, IconClose } from "@/components/navigation/icons";

function currentPageTitle(pathname: string): string {
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (item.to === "/" ? pathname === "/" : pathname.startsWith(item.to)) {
        return item.label;
      }
    }
  }
  return "Dashboard";
}

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const title = currentPageTitle(location.pathname);

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 border-r border-border bg-white lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setMobileOpen(false)} />
          <aside className="relative h-full w-64 bg-white shadow-popover">
            <SidebarContent />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-white px-4 lg:px-6">
          <button
            className="focus-ring -ml-1 rounded p-1.5 text-ink-muted hover:bg-neutral-100 lg:hidden"
            onClick={() => setMobileOpen((v) => !v)}
            aria-label="Toggle navigation"
          >
            {mobileOpen ? <IconClose className="h-5 w-5" /> : <IconMenu className="h-5 w-5" />}
          </button>
          <h1 className="text-sm font-semibold text-ink">{title}</h1>
        </header>

        <main className="flex-1 overflow-y-auto p-4 lg:p-6">
          <div className="mx-auto max-w-[1400px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
