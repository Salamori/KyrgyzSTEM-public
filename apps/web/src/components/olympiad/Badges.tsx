"use client";

import type { Band, ProblemSummary } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";

const BAND_STYLE: Record<Band, string> = {
  intro: "bg-sky-100 text-sky-900",
  easy: "bg-emerald-100 text-emerald-900",
  medium: "bg-amber-100 text-amber-900",
  hard: "bg-orange-100 text-orange-900",
  challenge: "bg-rose-100 text-rose-900",
};

/** Difficulty band plus who stands behind it; never presented as an official rating. */
export function BandBadge({
  band,
  status,
}: {
  band: Band;
  status: ProblemSummary["difficulty_status"];
}) {
  const { t } = useI18n();
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={`badge ${BAND_STYLE[band]}`}>{t(`band.${band}` as MessageKey)}</span>
      <span className="text-xs text-slate-600">{t(`bandStatus.${status}` as MessageKey)}</span>
    </span>
  );
}

export function MyStatusBadge({ status }: { status: ProblemSummary["my_status"] }) {
  const { t } = useI18n();
  if (!status) return null;
  const style =
    status === "solved"
      ? "bg-emerald-600 text-white"
      : status === "self_reported"
        ? "bg-slate-200 text-slate-800"
        : "bg-amber-100 text-amber-900";
  return <span className={`badge ${style}`}>{t(`myStatus.${status}` as MessageKey)}</span>;
}
