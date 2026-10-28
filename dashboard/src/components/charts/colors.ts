// Validated categorical/status palette from the dataviz reference instance
// (references/palette.md). Fixed order — never cycled arbitrarily, and never
// reassigned when a filter changes the series count.
export const CATEGORICAL = [
  "#2a78d6", // blue
  "#eb6834", // orange
  "#1baf7a", // aqua
  "#eda100", // yellow
  "#e87ba4", // magenta
  "#008300", // green
  "#4a3aa7", // violet
  "#e34948", // red
];

export const SEQUENTIAL_BLUE = "#2a78d6";
export const SEQUENTIAL_BLUE_FILL = "#cde2fb";

export const STATUS = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
};

export const CHART_INK = {
  secondary: "#52514e",
  muted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
};
