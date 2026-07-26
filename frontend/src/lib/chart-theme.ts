import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Title, Tooltip, Legend, Filler,
} from "chart.js"

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend, Filler)

// Fixed categorical order — validated (dataviz skill's validate_palette.js, all
// checks pass) against the dark card surface (#18181b). Assign in this order,
// never cycle or reassign per-filter.
export const CHART_COLORS = ["#4a8fe8", "#1fa860", "#c97a1f", "#e2496f", "#9d7ae0"]

export const CHART_GRID_COLOR = "rgba(255,255,255,0.06)"
export const CHART_TEXT_COLOR = "#a1a1aa"
export const CHART_FONT = "'Manrope', system-ui, sans-serif"

/** Standard bar styling: rounded tops, thin, with a soft gap between bars. */
export const barDatasetDefaults = {
  borderRadius: 6,
  borderSkipped: false as const,
  maxBarThickness: 40,
}

/** Standard line styling: thin, smooth, small points that grow on hover. */
export const lineDatasetDefaults = {
  borderWidth: 2.5,
  pointRadius: 0,
  pointHoverRadius: 5,
  pointHoverBorderWidth: 2,
  pointHoverBackgroundColor: "#18181b",
  tension: 0.35,
}

export const baseChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index" as const, intersect: false },
  animation: { duration: 400 },
  plugins: {
    legend: {
      labels: {
        color: CHART_TEXT_COLOR, boxWidth: 8, boxHeight: 8, usePointStyle: true,
        pointStyle: "circle", padding: 16, font: { family: CHART_FONT, size: 12 },
      },
    },
    tooltip: {
      backgroundColor: "#18181b",
      borderColor: "#27272a",
      borderWidth: 1,
      padding: 10,
      cornerRadius: 8,
      titleColor: "#fafafa",
      bodyColor: "#d4d4d8",
      titleFont: { family: CHART_FONT, size: 12, weight: 600 as const },
      bodyFont: { family: CHART_FONT, size: 12 },
      boxPadding: 4,
      usePointStyle: true,
    },
  },
  scales: {
    x: {
      grid: { color: "transparent" },
      border: { color: CHART_GRID_COLOR },
      ticks: { color: CHART_TEXT_COLOR, font: { family: CHART_FONT, size: 11 } },
    },
    y: {
      grid: { color: CHART_GRID_COLOR },
      border: { display: false },
      ticks: { color: CHART_TEXT_COLOR, font: { family: CHART_FONT, size: 11 } },
      beginAtZero: true,
    },
  },
}
