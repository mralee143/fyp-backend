"use client";

import type { ApexOptions } from "apexcharts";

import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ApexChart } from "@/components/apex-chart";
import type { AdminAnalytics } from "@/lib/admin";

/** Palette that reads well in both light and dark themes. */
const C = {
  indigo: "#6366f1",
  rose: "#f43f5e",
  emerald: "#10b981",
  amber: "#f59e0b",
  sky: "#0ea5e9",
  violet: "#8b5cf6",
};
const CATEGORY_PALETTE = [C.rose, C.amber, C.sky, C.violet, C.emerald, C.indigo];

const AXIS = "#94a3b8"; // slate-400 — legible on light and dark
const GRID = "rgba(148, 163, 184, 0.2)";

/** Options shared by every chart (fonts, toolbar off, no chart-level noise). */
const base: ApexOptions = {
  chart: {
    fontFamily: "inherit",
    toolbar: { show: false },
    zoom: { enabled: false },
    background: "transparent",
    animations: { speed: 500 },
  },
  grid: { borderColor: GRID, strokeDashArray: 4 },
  tooltip: { theme: "dark" },
  legend: { labels: { colors: AXIS }, fontSize: "13px" },
  dataLabels: { enabled: false },
  states: { hover: { filter: { type: "lighten" } } },
};

function labelStyle() {
  return { colors: AXIS, fontSize: "12px" };
}

export function AdminCharts({ data }: { data: AdminAnalytics }) {
  const days = data.scans_by_day;
  const hasCategories = data.categories.length > 0;
  const hasModels = data.models.length > 0;
  const hasUsers = data.top_users.length > 0;

  /* ---- Detection activity (area, 14 days) ---- */
  const activityOptions: ApexOptions = {
    ...base,
    colors: [C.indigo, C.rose],
    stroke: { curve: "smooth", width: 2 },
    fill: {
      type: "gradient",
      gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.05 },
    },
    xaxis: {
      categories: days.map((d) => d.day.slice(5)), // MM-DD
      labels: { style: labelStyle(), rotate: 0 },
      axisBorder: { color: GRID },
      axisTicks: { color: GRID },
      tooltip: { enabled: false },
    },
    yaxis: { labels: { style: labelStyle() }, min: 0, forceNiceScale: true },
  };
  const activitySeries = [
    { name: "Scans", data: days.map((d) => d.scans) },
    { name: "Threats", data: days.map((d) => d.threats) },
  ];

  /* ---- Threat ratio (donut) ---- */
  const ratioOptions: ApexOptions = {
    ...base,
    labels: ["Threats", "Clear"],
    colors: [C.rose, C.emerald],
    legend: { ...base.legend, position: "bottom" },
    plotOptions: {
      pie: {
        donut: {
          size: "68%",
          labels: {
            show: true,
            total: { show: true, label: "Total", color: AXIS },
            value: { color: AXIS },
          },
        },
      },
    },
  };
  const ratioSeries = [data.totals.threats, data.totals.clear];

  /* ---- Crime categories (horizontal bar) ---- */
  const catOptions: ApexOptions = {
    ...base,
    colors: CATEGORY_PALETTE,
    plotOptions: {
      bar: {
        horizontal: true,
        borderRadius: 4,
        barHeight: "60%",
        distributed: true,
      },
    },
    legend: { show: false },
    xaxis: {
      categories: data.categories.map((c) => c.category),
      labels: { style: labelStyle() },
      axisBorder: { color: GRID },
      axisTicks: { color: GRID },
    },
    yaxis: { labels: { style: labelStyle() } },
    dataLabels: {
      enabled: true,
      style: { colors: ["#fff"], fontSize: "12px" },
    },
  };
  const catSeries = [
    { name: "Detections", data: data.categories.map((c) => c.count) },
  ];

  /* ---- Model usage (donut) ---- */
  const modelOptions: ApexOptions = {
    ...base,
    labels: data.models.map((m) => m.model),
    colors: [C.indigo, C.sky, C.violet, C.amber, C.emerald, C.rose],
    legend: { ...base.legend, position: "bottom" },
    plotOptions: { pie: { donut: { size: "62%" } } },
  };
  const modelSeries = data.models.map((m) => m.count);

  /* ---- Busiest users (bar) ---- */
  const userOptions: ApexOptions = {
    ...base,
    colors: [C.sky],
    plotOptions: { bar: { borderRadius: 4, columnWidth: "45%" } },
    xaxis: {
      categories: data.top_users.map((u) => u.email.split("@")[0]),
      labels: { style: labelStyle(), rotate: -25, trim: true, maxHeight: 60 },
      axisBorder: { color: GRID },
      axisTicks: { color: GRID },
    },
    yaxis: { labels: { style: labelStyle() }, min: 0, forceNiceScale: true },
  };
  const userSeries = [
    { name: "Scans", data: data.top_users.map((u) => u.count) },
  ];

  return (
    <div className="mt-8 grid gap-4 lg:grid-cols-3">
      {/* Activity spans two columns */}
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">Detection activity</CardTitle>
          <CardDescription>Scans and threats over the last 14 days</CardDescription>
        </CardHeader>
        <div className="px-2 pb-2">
          <ApexChart type="area" options={activityOptions} series={activitySeries} height={300} />
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Threat ratio</CardTitle>
          <CardDescription>Flagged vs clear</CardDescription>
        </CardHeader>
        <div className="px-2 pb-4">
          <ApexChart type="donut" options={ratioOptions} series={ratioSeries} height={280} />
        </div>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">Crime categories</CardTitle>
          <CardDescription>What the models flagged across all scans</CardDescription>
        </CardHeader>
        <div className="px-2 pb-2">
          {hasCategories ? (
            <ApexChart type="bar" options={catOptions} series={catSeries} height={280} />
          ) : (
            <Empty />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Model usage</CardTitle>
          <CardDescription>Which engine ran each scan</CardDescription>
        </CardHeader>
        <div className="px-2 pb-4">
          {hasModels ? (
            <ApexChart type="donut" options={modelOptions} series={modelSeries} height={260} />
          ) : (
            <Empty />
          )}
        </div>
      </Card>

      <Card className="lg:col-span-3">
        <CardHeader>
          <CardTitle className="text-base">Busiest users</CardTitle>
          <CardDescription>Users with the most scans</CardDescription>
        </CardHeader>
        <div className="px-2 pb-2">
          {hasUsers ? (
            <ApexChart type="bar" options={userOptions} series={userSeries} height={280} />
          ) : (
            <Empty />
          )}
        </div>
      </Card>
    </div>
  );
}

function Empty() {
  return (
    <div className="grid h-[240px] place-items-center text-sm text-muted-foreground">
      No data yet — run some detections first.
    </div>
  );
}
