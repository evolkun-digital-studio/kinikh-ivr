import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "@/components/cards/Card";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { CHART_INK, SEQUENTIAL_BLUE, SEQUENTIAL_BLUE_FILL } from "@/components/charts/colors";

interface Point {
  date: string;
  value: number;
}

export function TimeSeriesChart({
  title,
  data,
  isLoading,
  valueLabel,
  formatValue = (v) => String(v),
  emptyLabel,
}: {
  title: string;
  data: Point[];
  isLoading?: boolean;
  valueLabel: string;
  formatValue?: (value: number) => string;
  emptyLabel: string;
}) {
  return (
    <Card title={title}>
      {isLoading ? (
        <Skeleton className="h-56 w-full" />
      ) : data.length === 0 ? (
        <EmptyState title={emptyLabel} />
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <defs>
              <linearGradient id="tsFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={SEQUENTIAL_BLUE_FILL} stopOpacity={0.7} />
                <stop offset="100%" stopColor={SEQUENTIAL_BLUE_FILL} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={CHART_INK.grid} strokeDasharray="0" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: CHART_INK.muted }}
              axisLine={{ stroke: CHART_INK.axis }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              tick={{ fontSize: 11, fill: CHART_INK.muted }}
              axisLine={false}
              tickLine={false}
              width={36}
              allowDecimals={false}
            />
            <Tooltip
              formatter={(value: number) => [formatValue(value), valueLabel]}
              contentStyle={{
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid #e1e0d9",
                boxShadow: "0 4px 16px -2px rgb(0 0 0 / 0.10)",
              }}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={SEQUENTIAL_BLUE}
              strokeWidth={2}
              fill="url(#tsFill)"
              isAnimationActive={false}
              dot={false}
              activeDot={{ r: 4 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}
