// Small, consistent stroke-icon set (Feather/Lucide-style) used across the
// shell -- kept in one file rather than pulling in an icon library dependency
// for a fixed, known set of glyphs.

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

function Icon({ children, className = "w-5 h-5" }) {
  return (
    <svg {...base} className={className} aria-hidden="true">
      {children}
    </svg>
  );
}

export function IconDashboard(props) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </Icon>
  );
}

export function IconRoute(props) {
  return (
    <Icon {...props}>
      <circle cx="6" cy="19" r="2.5" />
      <circle cx="18" cy="5" r="2.5" />
      <path d="M8.2 17.5 15.8 6.5" strokeDasharray="2.5 3" />
    </Icon>
  );
}

export function IconStation(props) {
  return (
    <Icon {...props}>
      <path d="M12 21c4-4.2 7-7.8 7-11.2A7 7 0 0 0 5 9.8C5 13.2 8 16.8 12 21Z" />
      <path d="M13.2 6.5 10 12h3l-1.2 5.5L15.5 11h-3l0.7-4.5Z" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function IconAnalytics(props) {
  return (
    <Icon {...props}>
      <path d="M4 20V10M12 20V4M20 20v-7" />
      <path d="M3 20h18" strokeWidth="1.5" />
    </Icon>
  );
}

export function IconBookmark(props) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h12a1 1 0 0 1 1 1V21l-7-4-7 4V4.5a1 1 0 0 1 1-1Z" />
    </Icon>
  );
}

export function IconUser(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20c1.4-3.8 4.4-5.8 7.5-5.8s6.1 2 7.5 5.8" />
    </Icon>
  );
}

export function IconSettings(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 13.5a1.7 1.7 0 0 0 .35 1.9l.06.06a2 2 0 1 1-2.9 2.9l-.06-.06a1.7 1.7 0 0 0-1.9-.35 1.7 1.7 0 0 0-1 1.6V20a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.35l-.06.06a2 2 0 1 1-2.9-2.9l.06-.06a1.7 1.7 0 0 0 .35-1.9 1.7 1.7 0 0 0-1.6-1H4a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.35-1.9l-.06-.06a2 2 0 1 1 2.9-2.9l.06.06a1.7 1.7 0 0 0 1.9.35H10a1.7 1.7 0 0 0 1-1.6V4a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.35l.06-.06a2 2 0 1 1 2.9 2.9l-.06.06a1.7 1.7 0 0 0-.35 1.9V10a1.7 1.7 0 0 0 1.6 1H20a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.6 1Z" />
    </Icon>
  );
}

export function IconShield(props) {
  return (
    <Icon {...props}>
      <path d="M12 3 5 6v5.5c0 4.6 3 7.7 7 9.5 4-1.8 7-4.9 7-9.5V6l-7-3Z" />
    </Icon>
  );
}

export function IconSearch(props) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-3.6-3.6" />
    </Icon>
  );
}

export function IconBell(props) {
  return (
    <Icon {...props}>
      <path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 14 6 10Z" />
      <path d="M9.5 18.5a2.5 2.5 0 0 0 5 0" />
    </Icon>
  );
}

export function IconMapPin(props) {
  return (
    <Icon {...props}>
      <path d="M12 21s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12Z" />
      <circle cx="12" cy="9" r="2.5" />
    </Icon>
  );
}

export function IconNavigation(props) {
  return (
    <Icon {...props}>
      <path d="m3 11 18-8-8 18-2.5-7.5L3 11Z" />
    </Icon>
  );
}

export function IconClose(props) {
  return (
    <Icon {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Icon>
  );
}

export function IconBolt(props) {
  return (
    <Icon {...props}>
      <path d="M13 2 4 14h7l-1 8 10-12h-7l1-8Z" />
    </Icon>
  );
}

export function IconClock(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </Icon>
  );
}

export function IconGauge(props) {
  return (
    <Icon {...props}>
      <path d="M4 15a8 8 0 1 1 16 0" />
      <path d="M12 15 15.5 9.5" />
      <path d="M4 15h16" strokeWidth="1.5" />
    </Icon>
  );
}

export function IconCar(props) {
  return (
    <Icon {...props}>
      <path d="M4.5 16v-3.2c0-.5.2-1 .55-1.35l1.7-1.7c.35-.35.83-.55 1.33-.55h7.84c.5 0 .98.2 1.33.55l1.7 1.7c.35.35.55.85.55 1.35V16" />
      <path d="M3.5 16h17v2.2a.8.8 0 0 1-.8.8h-1.4a.8.8 0 0 1-.8-.8V17H6.5v1.2a.8.8 0 0 1-.8.8H4.3a.8.8 0 0 1-.8-.8V16Z" />
      <circle cx="7.5" cy="16" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="16.5" cy="16" r="1.4" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function IconChevronDown(props) {
  return (
    <Icon {...props}>
      <path d="m6 9 6 6 6-6" />
    </Icon>
  );
}

export function IconExpand(props) {
  return (
    <Icon {...props}>
      <path d="M9 3H3v6M15 3h6v6M9 21H3v-6M15 21h6v-6" />
    </Icon>
  );
}

export function IconCompass(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="m15 9-4.5 1.5L9 15l4.5-1.5L15 9Z" />
    </Icon>
  );
}

export function IconPlus(props) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function IconMinus(props) {
  return (
    <Icon {...props}>
      <path d="M5 12h14" />
    </Icon>
  );
}

export function IconWallet(props) {
  return (
    <Icon {...props}>
      <path d="M3.5 7.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-9Z" />
      <path d="M15.5 12.5h3v3h-3a1.5 1.5 0 0 1 0-3Z" />
    </Icon>
  );
}

export function IconScale(props) {
  return (
    <Icon {...props}>
      <path d="M12 3v18M7 7h10M4.5 7 2.5 12a2.2 2.2 0 0 0 4 0L4.5 7ZM19.5 7l-2 5a2.2 2.2 0 0 0 4 0l-2-5Z" />
    </Icon>
  );
}

export function IconLeaf(props) {
  return (
    <Icon {...props}>
      <path d="M5 19c-1-6 2.5-13 14-14-1 11-7 14.5-14 14Z" />
      <path d="M5.5 18.5 14 10" />
    </Icon>
  );
}
