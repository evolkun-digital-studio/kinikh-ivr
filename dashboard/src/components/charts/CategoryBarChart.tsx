import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card } from "@/components/cards/Card";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { CATEGORICAL, CHART_INK } from "@/components/charts/colors";

interface Point {
  label: string;
  value: number;
}

export function CategoryBarChart({
  title,
  data,
  isLoading,
  emptyLabel,
}: {
  title: string;
  data: Point[];
  isLoading?: boolean;
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
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid vertical={false} stroke={CHART_INK.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: CHART_INK.muted }}
              axisLine={{ stroke: CHART_INK.axis }}
              tickLine={false}
              interval={0}
              angle={data.length > 5 ? -20 : 0}
              textAnchor={data.length > 5 ? "end" : "middle"}
              height={data.length > 5 ? 40 : 24}
            />
            <YAxis
              tick={{ fontSize: 11, fill: CHART_INK.muted }}
              axisLine={false}
              tickLine={false}
              width={32}
              allowDecimals={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(11,11,11,0.04)" }}
              contentStyle={{
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid #e1e0d9",
                boxShadow: "0 4px 16px -2px rgb(0 0 0 / 0.10)",
              }}
              isAnimationActive={false}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} isAnimationActive={false} maxBarSize={40}>
              {data.map((_, i) => (
                <Cell key={i} fill={CATEGORICAL[i % CATEGORICAL.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}
