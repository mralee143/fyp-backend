"use client";

import { useEffect, useRef } from "react";
import type { ApexOptions } from "apexcharts";

/**
 * Thin client-only wrapper around ApexCharts.
 *
 * We drive the `apexcharts` package directly (rather than react-apexcharts)
 * because the React wrapper doesn't declare React 19 as a supported peer.
 * The library is imported lazily inside an effect so it never runs on the
 * server (ApexCharts needs `window`).
 */
export function ApexChart({
  type,
  series,
  options,
  height = 320,
}: {
  type: NonNullable<ApexOptions["chart"]>["type"];
  series: ApexOptions["series"];
  options: ApexOptions;
  height?: number;
}) {
  const elRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);

  useEffect(() => {
    let disposed = false;

    import("apexcharts").then(({ default: ApexCharts }) => {
      if (disposed || !elRef.current) return;

      const merged: ApexOptions = {
        ...options,
        chart: { ...options.chart, type, height },
        series,
      };

      chartRef.current = new ApexCharts(elRef.current, merged);
      chartRef.current.render();
    });

    return () => {
      disposed = true;
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
    // Re-render on any input change. options/series are recreated each render
    // by callers, so stringify to compare by value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, height, JSON.stringify(series), JSON.stringify(options)]);

  return <div ref={elRef} />;
}
