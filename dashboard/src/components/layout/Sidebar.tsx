import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "@/components/navigation/navConfig";
import { IconLogout } from "@/components/navigation/icons";
import { useAuth } from "@/hooks/useAuth";

export function SidebarContent() {
  const { logout } = useAuth();

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2 border-b border-border px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-ink text-xs font-bold text-white">
          K
        </div>
        <span className="text-sm font-semibold text-ink">Kinikh IVR</span>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-5">
            <p className="mb-1.5 px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
              {group.label}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    `focus-ring flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-neutral-100 text-ink"
                        : "text-ink-muted hover:bg-neutral-50 hover:text-ink"
                    }`
                  }
                >
                  <item.icon className="h-[18px] w-[18px] shrink-0" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-border p-3">
        <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-hover">
            A
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-ink">Admin</p>
            <p className="truncate text-xs text-ink-faint">Operator account</p>
          </div>
          <button
            onClick={logout}
            aria-label="Log out"
            className="focus-ring rounded p-1.5 text-ink-muted hover:bg-neutral-100 hover:text-ink"
          >
            <IconLogout className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
