/**
 * Chart palette.
 *
 * These are the validated categorical slots in their validated order. The order
 * is the colourblind-safety mechanism, not decoration -- slots are assigned in
 * sequence and never cycled, so a category keeps one hue everywhere it appears.
 * The dark column is the same eight hues re-stepped for the dark surface, not an
 * automatic inversion.
 */

export const CATEGORICAL_LIGHT = [
  "#2a78d6", // blue
  "#eb6834", // orange
  "#1baf7a", // aqua
  "#eda100", // yellow
  "#e87ba4", // magenta
  "#008300", // green
  "#4a3aa7", // violet
  "#e34948", // red
];

export const CATEGORICAL_DARK = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300",
  "#9085e9",
  "#e66767",
];

export function chartTheme(isDark) {
  return {
    series: isDark ? CATEGORICAL_DARK : CATEGORICAL_LIGHT,
    surface: isDark ? "#1a1a19" : "#fcfcfb",
    grid: isDark ? "#35342f" : "#e7e5df",
    axis: isDark ? "#8f8e85" : "#86847d",
    text: isDark ? "#ffffff" : "#0b0b0b",
    textSecondary: isDark ? "#c3c2b7" : "#52514e",
    border: isDark ? "#4a4943" : "#dedcd6",
  };
}

/** Slot index for a value, fixed by its position in the vocabulary so a series
 *  never changes colour when the set on screen changes. */
export function slotColor(theme, index) {
  return theme.series[index % theme.series.length];
}
