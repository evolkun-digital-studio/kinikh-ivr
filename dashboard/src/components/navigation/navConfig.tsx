import {
  IconBuilding,
  IconChart,
  IconDocument,
  IconDollar,
  IconGrid,
  IconMail,
  IconMic,
  IconPhone,
  IconSettings,
  IconUsers,
} from "@/components/navigation/icons";

export interface NavItem {
  label: string;
  to: string;
  icon: (props: { className?: string }) => JSX.Element;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [{ label: "Dashboard", to: "/", icon: IconGrid }],
  },
  {
    label: "Call Management",
    items: [
      { label: "Calls", to: "/calls", icon: IconPhone },
      { label: "Transcripts", to: "/transcripts", icon: IconDocument },
      { label: "Recordings", to: "/recordings", icon: IconMic },
    ],
  },
  {
    label: "Leads",
    items: [{ label: "Leads", to: "/leads", icon: IconUsers }],
  },
  {
    label: "Business",
    items: [{ label: "Departments", to: "/departments", icon: IconBuilding }],
  },
  {
    label: "Communication",
    items: [{ label: "Emails", to: "/emails", icon: IconMail }],
  },
  {
    label: "Analytics",
    items: [
      { label: "Analytics", to: "/analytics", icon: IconChart },
      { label: "API Usage & Costs", to: "/usage", icon: IconDollar },
    ],
  },
  {
    label: "System",
    items: [{ label: "Settings", to: "/settings", icon: IconSettings }],
  },
];
